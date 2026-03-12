# admin.py
import os
import io
import uuid
import asyncio
from urllib.parse import urlparse, unquote
from typing import List, Optional, Tuple, Literal
from datetime import datetime, date, time
from pathlib import Path
import mimetypes
import boto3
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from botocore.exceptions import NoCredentialsError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc

# ------------------ app imports ------------------
from app.database import get_db
from app.utils.helper import require_hr_or_superadmin
from app.utils.guards import hr_session_required
import json
from app.models.screening_models import ResumeScreening
from app.models.user_models import CandidateProfileInformation, CandidateDocument
from app.models.job_models import (
    Job, Application, ApplicationSource, Interview,
    InterviewType, RoundStatus
)
from app.schemas.interview_schemas import (
    PanelUpdate, InterviewCreate, InterviewUpdateResult, InterviewReschedule,
    ReasonOnly, InterviewOut, AdminApplicationRowOut, AdminApplicationDetailOut,AdminApplicationsPageOut
)
from app.schemas.jobs_schemas import ApplicationStatusUpdate
from app.utils.aws_email import aws_send_mail
from app.schemas.user_schemas import NotificationStructure
from app.utils.notify_depends import send_notification_to_candidate as _notify_candidate_async
from app.utils.s3_utils_1 import put_bytes  # using your SMTP mail sender
from app.utils.s3_utils import generate_http_url

# =============================================================================
# Shared Helpers (Used by both Interviews & Applications)
# =============================================================================
def _email_creds() -> dict:
    return {
        "FROM_EMAIL": "careers@securxperts.com",  # verified in SES
    }

async def _send_email_async(
    to: List[str],
    subject: str,
    body: str,
    body_type: Literal["plain", "html"] = "plain",
):
    creds = _email_creds()

    await aws_send_mail(
        from_email=creds["FROM_EMAIL"],
        reciver_to=to,
        subject=subject,
        body=body,
        body_type=body_type,
    )

def _send_email_now(
    to: List[str],
    subject: str,
    body: str,
    body_type: Literal["plain", "html"] = "plain",
) -> tuple[bool, Optional[str]]:
    """
    Safe email sender with auto async/sync fallback
    """
    try:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_email_async(to, subject, body, body_type))
        except RuntimeError:
            asyncio.run(_send_email_async(to, subject, body, body_type))
        return True, None
    except Exception as e:
        return False, str(e)


def _notify_candidate_sync(candidate_id: str, notif: NotificationStructure) -> None:
    payload = notif.model_dump() if hasattr(notif, "model_dump") else notif.dict()
    try:
        asyncio.run(_notify_candidate_async(candidate_id, payload))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(_notify_candidate_async(candidate_id, payload))

# =============================================================================
# Interviews Section
# =============================================================================

def _panel_feedback_submit_base() -> str:
    base = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return base + "/panel/feedback/submit/"

def _ensure_per_panelist_tokens(db: Session, interview: Interview):
    changed = False
    updated = []

    for pm in (interview.panel_members or []):
        d = dict(pm)

        if not d.get("feedback_token"):
            d["feedback_token"] = uuid.uuid4().hex
            changed = True

        if not d.get("feed_back_link"):
            d["feed_back_link"] = _panel_feedback_submit_base() + d["feedback_token"]
            changed = True

        if "submitted" not in d:
            d["submitted"] = False
            changed = True

        if "submitted_at" not in d:
            d["submitted_at"] = None
            changed = True

        updated.append(d)

    if updated != interview.panel_members:
        interview.panel_members = updated
        changed = True

    if changed:
        db.commit()
        db.refresh(interview)

# =============================================================================
# in-app notification helpers
# =============================================================================

def _notif_interview(
    interview: Interview,
    kind: Literal["scheduled", "rescheduled", "cancelled"],
    old_slot: Optional[Tuple[date, time, time, str]] = None
) -> NotificationStructure:
    round_cat = getattr(interview, "round_category", "technical")
    level_str = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")

    scenario_map = {
        "scheduled": "interview_scheduled",
        "rescheduled": "interview_rescheduled",
        "cancelled": "interview_cancelled"
    }
    scenario = scenario_map[kind]

    message = f"{level_str} {kind} for Job {interview.job_id}"

    data = {
        "interview_id": interview.interview_id,
        "candidate_id": interview.candidate_id,
        "job_id": interview.job_id,
        "job_title": getattr(interview, "technology", ""),
        "level_number": interview.level_number,
        "level_label": level_str,
        "date": interview.date.isoformat(),
        "start_time": interview.start_time.strftime("%H:%M"),
        "end_time": interview.end_time.strftime("%H:%M"),
        "timezone": interview.interview_timezone,
        "interview_type": (
            interview.interview_type.value
            if hasattr(interview.interview_type, "value")
            else str(interview.interview_type)
        ),
        "interview_link": interview.interview_link,
        "location_full": interview.location_full,
        "panel": ", ".join(
            [p.get("name") for p in (interview.panel_members or []) if p.get("name")]
        ),
        "reason": interview.reason,
        "updated_at_utc": datetime.utcnow().isoformat(),
    }

    if old_slot:
        od, ost, oet, otz = old_slot
        data.update({
            "old_date": od.isoformat(),
            "old_start_time": ost.strftime("%H:%M"),
            "old_end_time": oet.strftime("%H:%M"),
            "old_timezone": otz,
        })

    return NotificationStructure(
        scenario=scenario,
        message=message,
        meta_data={
            # FROM YOUR SWAGGER SCREENSHOT
            "route_path": f"/interviews/{interview.interview_id}",
            "http_method": "GET",
        },
        data=data,
    )


# ==========================================================
# Email Templates
# ==========================================================

def _fmt_slot_lines(d: date, start: time, end: time, tz: str):
    return f"Date: {d}\nTime: {start.strftime('%H:%M')}–{end.strftime('%H:%M')} ({tz})\n"

def _panel_names(interview: Interview):
    names = [pm.get("name") for pm in (interview.panel_members or []) if pm.get("name")]
    return ", ".join(names)

def _scheduled_email_candidate(interview: Interview):
    round_cat = getattr(interview, "round_category", "technical")
    level = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")
    subject = f"{level} Scheduled — Job {interview.job_id}"
    slot = _fmt_slot_lines(interview.date, interview.start_time, interview.end_time, interview.interview_timezone)
    venue = f"Join Link: {interview.interview_link}\n" if interview.interview_type == InterviewType.virtual else f"Location: {interview.location_full}\n"
    body = (
        f"Dear {interview.candidate_first_name},\n\n"
        f"Your {level} interview has been scheduled for Job ID: {interview.job_id}.\n\n"
        f"{slot}"
        f"Type: {interview.interview_type}\n"
        f"{venue}"
        f"Panel: {_panel_names(interview)}\n\n"
        f"Please be available 10 minutes prior.\n\nRegards,\nRecruitment Team"
    )
    return subject, body

def _scheduled_email_panel_member(interview: Interview, pm: dict):
    round_cat = getattr(interview, "round_category", "technical")
    level = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")
    subject = f"Action Required: Conduct {level} — {interview.candidate_first_name} {interview.candidate_last_name} (Job {interview.job_id})"
    slot = _fmt_slot_lines(interview.date, interview.start_time, interview.end_time, interview.interview_timezone)
    venue = f"Join Link: {interview.interview_link}\n" if interview.interview_type == InterviewType.virtual else f"Location: {interview.location_full}\n"
    feedback = f"\nSubmit feedback: {pm.get('feed_back_link')}\n"
    body = (
        f"Dear {pm.get('name','Panel Member')},\n\n"
        f"You are scheduled to conduct {level}.\n\n"
        f"Candidate: {interview.candidate_first_name} {interview.candidate_last_name}\n"
        f"Email: {interview.candidate_email}\n"
        f"Job: {interview.job_id}\n"
        f"Tech: {interview.technology}\n\n"
        f"{slot}{venue}"
        f"Panel: {_panel_names(interview)}\n"
        f"{feedback}\nRegards,\nRecruitment Team"
    )
    return subject, body

def _rescheduled_email_candidate(interview: Interview, old):
    round_cat = getattr(interview, "round_category", "technical")
    level = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")
    subject = f"Interview Rescheduled — {level} (Job {interview.job_id})"

    od, ost, oet, otz = old
    old_info = (
        "Previous Schedule:\n"
        f"{_fmt_slot_lines(od, ost, oet, otz)}\n"
    )
    new_info = (
        "New Schedule:\n"
        f"{_fmt_slot_lines(interview.date, interview.start_time, interview.end_time, interview.interview_timezone)}\n"
    )

    venue = (
        f"Join Link: {interview.interview_link}\n"
        if interview.interview_type == InterviewType.virtual
        else f"Location: {interview.location_full}\n"
    )

    body = (
        f"Dear {interview.candidate_first_name},\n\n"
        f"Your {level} interview has been rescheduled.\n\n"
        f"{old_info}"
        f"{new_info}"
        f"{venue}"
        f"Reason for rescheduling: {interview.reason or 'N/A'}\n\n"
        f"Please ensure you are available at the new time.\n\n"
        f"Regards,\nRecruitment Team"
    )
    return subject, body

def _rescheduled_email_panel_member(interview: Interview, pm: dict, old):
    round_cat = getattr(interview, "round_category", "technical")
    level = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")
    subject = f"Rescheduled: {level} — {interview.candidate_first_name} {interview.candidate_last_name}"

    od, ost, oet, otz = old
    old_info = (
        "Previous Schedule:\n"
        f"{_fmt_slot_lines(od, ost, oet, otz)}\n"
    )
    new_info = (
        "New Schedule:\n"
        f"{_fmt_slot_lines(interview.date, interview.start_time, interview.end_time, interview.interview_timezone)}\n"
    )

    venue = (
        f"Join Link: {interview.interview_link}\n"
        if interview.interview_type == InterviewType.virtual
        else f"Location: {interview.location_full}\n"
    )

    body = (
        f"Dear {pm.get('name', 'Panel Member')},\n\n"
        f"The interview with {interview.candidate_first_name} {interview.candidate_last_name} has been rescheduled.\n\n"
        f"{old_info}"
        f"{new_info}"
        f"{venue}"
        f"Reason for rescheduling: {interview.reason or 'N/A'}\n\n"
        f"Feedback Link: {pm.get('feed_back_link')}\n\n"
        f"Regards,\nRecruitment Team"
    )
    return subject, body

def _cancelled_email_candidate(interview: Interview):
    round_cat = getattr(interview, "round_category", "technical")
    level = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")
    subject = f"Interview Cancelled — {level} (Job {interview.job_id})"
    body = (
        f"Dear {interview.candidate_first_name},\n\n"
        f"Your {level} interview has been cancelled.\n"
        f"Reason: {interview.reason or 'N/A'}\n\n"
        f"Regards,\nRecruitment Team"
    )
    return subject, body

def _cancelled_email_panel_member(interview: Interview, pm: dict):
    round_cat = getattr(interview, "round_category", "technical")
    level = "HR Round" if round_cat == "hr" else (interview.round_name or f"L{interview.level_number}")
    subject = f"Cancelled: {level} — {interview.candidate_first_name} {interview.candidate_last_name}"
    body = (
        f"Dear {pm.get('name','Panel Member')},\n\n"
        f"The {level} interview has been cancelled.\n"
        f"Candidate: {interview.candidate_first_name} {interview.candidate_last_name}\n"
        f"Reason: {interview.reason or 'N/A'}\n\n"
        f"Regards,\nRecruitment Team"
    )
    return subject, body

def _result_email(interview: Interview, status: str):
    round_cat = getattr(interview, "round_category", "technical")
    level = (
        "HR Round"
        if round_cat == "hr"
        else (interview.round_name or f"L{interview.level_number}")
    )

    if status == "passed":
        subject = f"Congratulations — You Passed {level}"
        body = (
            f"Dear {interview.candidate_first_name},\n\n"
            f"Congratulations! You have successfully passed your {level} interview.\n"
            f"Our team will get in touch with you regarding the next steps.\n\n"
            f"Regards,\n"
            f"Recruitment Team"
        )

    else:    # failed
        subject = f"Interview Result — {level}"
        body = (
            f"Dear {interview.candidate_first_name},\n\n"
            f"Thank you for your time and effort in attending the {level} interview.\n"
            f"After careful evaluation, we regret to inform you that you did not pass this round.\n\n"
            f"We appreciate your interest in joining our team and wish you the best in your future endeavors.\n\n"
            f"Regards,\n"
            f"Recruitment Team"
        )

    return subject, body, [interview.candidate_email]



# Interviews Router
admin_interviews_router = APIRouter(
    prefix="/admin/interviews",
    tags=["Admin - Interviews"],
    dependencies=[Depends(require_hr_or_superadmin), Depends(hr_session_required)],
)

@admin_interviews_router.post("/schedule", response_model=InterviewOut,
                              status_code=status.HTTP_201_CREATED,
                              response_model_exclude_none=True)
def schedule_interview(payload: InterviewCreate, db: Session = Depends(get_db)):
    """
    FINAL INTERVIEW SCHEDULING LOGIC

    ✔ Multiple categories allowed (technical, managerial, behavioural, hr)
    ✔ Each category can have multiple levels (L1, L2, L3…)
    ✔ Duplicate = same (candidate + job + category + level)
    ✔ Level dependency ONLY inside same category
    ✔ Category order NOT enforced → ANY order allowed
    ✔ HR shortlist check ONLY for FIRST EVER interview
    """

    cand = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == payload.candidate_id
    ).first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")

    app = db.query(Application).filter(
        Application.candidate_id == payload.candidate_id,
        Application.job_id == payload.job_id
    ).first()
    if not app:
        raise HTTPException(status_code=400, detail="Application not found")

    existing_interviews = db.query(Interview).filter(
        Interview.candidate_id == payload.candidate_id,
        Interview.job_id == payload.job_id
    ).all()

    if len(existing_interviews) == 0:  # First interview only
        if app.status.lower() != "shortlisted":
            raise HTTPException(
                status_code=400,
                detail="Candidate is not shortlisted. HR approval required before scheduling the first interview."
            )

    dup = db.query(Interview).filter(
        Interview.candidate_id == payload.candidate_id,
        Interview.job_id == payload.job_id,
        Interview.round_category == payload.round_category,
        Interview.level_number == payload.level_number
    ).first()

    if dup:
        raise HTTPException(
            status_code=400,
            detail=f"Level {payload.level_number} already exists in category '{payload.round_category}'."
        )

    if payload.level_number > 1:
        previous_level = payload.level_number - 1

        prev_round = db.query(Interview).filter(
            Interview.candidate_id == payload.candidate_id,
            Interview.job_id == payload.job_id,
            Interview.round_category == payload.round_category,
            Interview.level_number == previous_level
        ).first()

        if not prev_round:
            raise HTTPException(
                status_code=400,
                detail=f"{payload.round_category} Level {previous_level} must exist before Level {payload.level_number}."
            )

        if prev_round.status.value.lower() != "passed":
            raise HTTPException(
                status_code=400,
                detail=f"{payload.round_category} Level {previous_level} is not passed."
            )

    interview = Interview(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        candidate_first_name=cand.first_name,
        candidate_last_name=cand.last_name,
        candidate_email=cand.email,

        experience_type=payload.experience_type,
        total_experience_years=payload.total_experience_years,
        relevant_experience_years=payload.relevant_experience_years,

        technology=payload.technology,
        level_number=payload.level_number,
        round_category=payload.round_category,
        round_name=payload.round_name,

        date=payload.date,
        start_time=payload.start_time,
        end_time=payload.end_time,
        interview_timezone=payload.interview_timezone or "Asia/Kolkata",

        interview_type=payload.interview_type,
        interview_link=payload.interview_link,
        location_full=payload.location_full,

        panel_members=[pm.dict() for pm in payload.panel_members],

        created_by=payload.created_by,
        status=RoundStatus.pending,
    )

    db.add(interview)

    # Update application status
    app.status = "interview_scheduled"

    db.commit()
    db.refresh(interview)

    # Token generation for panel
    _ensure_per_panelist_tokens(db, interview)

    # Candidate email
    subj, body = _scheduled_email_candidate(interview)
    _send_email_now([interview.candidate_email], subj, body)

    # In-app notification
    _notify_candidate_sync(
        interview.candidate_id,
        _notif_interview(interview, "scheduled")
    )

    # Panel emails
    for pm in interview.panel_members:
        if pm.get("email"):
            ps, pb = _scheduled_email_panel_member(interview, pm)
            _send_email_now([pm["email"]], ps, pb)

    return interview

@admin_interviews_router.patch(
    "/{interview_id}/panel",
    response_model=InterviewOut,
    response_model_exclude_none=True,
)
def update_panel(interview_id: int, payload: PanelUpdate, db: Session = Depends(get_db)):
    """
    Update panel members for an interview.

    ✔ Removed members → Cancellation email
    ✔ Added members → Assignment email
    ✔ Unchanged members → No email
    ✔ Tokens preserved for unchanged members
    ✔ Tokens generated only for new members
    """

    obj = db.query(Interview).filter(Interview.interview_id == interview_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Interview not found")

    if not (1 <= len(payload.panel_members) <= 6):
        raise HTTPException(status_code=400, detail="Panel count must be 1–6")

    for pm in payload.panel_members:
        if not pm.name or not pm.email:
            raise HTTPException(status_code=400, detail="Panel name + email required")

    old_panel = obj.panel_members or []

    # map by email (email is unique & stable)
    old_map = {p["email"]: p for p in old_panel if p.get("email")}
    new_map = {pm.email: pm.dict() for pm in payload.panel_members}

    old_emails = set(old_map.keys())
    new_emails = set(new_map.keys())

    removed_emails = old_emails - new_emails
    added_emails = new_emails - old_emails
    unchanged_emails = old_emails & new_emails

    for email in removed_emails:
        pm = old_map[email]
        subject, body = _cancelled_email_panel_member(obj, pm)
        _send_email_now([email], subject, body)

    final_panel: list[dict] = []

    for email in unchanged_emails:
        final_panel.append(old_map[email])

    for email in added_emails:
        final_panel.append(new_map[email])

    obj.panel_members = final_panel
    db.commit()
    db.refresh(obj)

    _ensure_per_panelist_tokens(db, obj)
    db.refresh(obj)

    for pm in obj.panel_members:
        if pm.get("email") in added_emails:
            subject, body = _scheduled_email_panel_member(obj, pm)
            _send_email_now([pm["email"]], subject, body)

    return obj


@admin_interviews_router.patch("/{interview_id}/reschedule", response_model=InterviewOut, response_model_exclude_none=True)
def reschedule_interview(interview_id: int, payload: InterviewReschedule, db: Session = Depends(get_db)):
    """
    Real-world rescheduling:
    - Direct reschedule (no need to cancel first)
    - Only date/time changes
    - Join link remains same
    - Panel remains same
    - Feedback tokens remain same
    - Emails include old + new schedule
    - Application status also updated (new requirement)
    """

    obj = db.query(Interview).filter(Interview.interview_id == interview_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Prevent rescheduling after completion
    if obj.status in [RoundStatus.passed, RoundStatus.failed]:
        raise HTTPException(status_code=400, detail="Completed interview cannot be rescheduled.")

    old_slot = (
        obj.date,
        obj.start_time,
        obj.end_time,
        obj.interview_timezone
    )

    obj.date = payload.date
    obj.start_time = payload.start_time
    obj.end_time = payload.end_time
    obj.interview_timezone = payload.interview_timezone or obj.interview_timezone

    obj.reason = payload.reason.strip() if payload.reason else None

    # Update interview status
    obj.status = RoundStatus.rescheduled

    app = db.query(Application).filter(
        Application.candidate_id == obj.candidate_id,
        Application.job_id == obj.job_id
    ).first()

    if app:
        app.status = "interview_rescheduled"

    db.commit()
    db.refresh(obj)

    # Ensure tokens exist (no regeneration on reschedule)
    _ensure_per_panelist_tokens(db, obj)

    subj, body = _rescheduled_email_candidate(obj, old_slot)
    _send_email_now([obj.candidate_email], subj, body)

    # In-app notification
    _notify_candidate_sync(
        obj.candidate_id,
        _notif_interview(obj, "rescheduled", old_slot)
    )

    for pm in obj.panel_members:
        if pm.get("email"):
            ps, pb = _rescheduled_email_panel_member(obj, pm, old_slot)
            _send_email_now([pm["email"]], ps, pb)

    return obj


@admin_interviews_router.patch("/{interview_id}/cancel", response_model=InterviewOut, response_model_exclude_none=True)
def cancel_interview(interview_id: int, payload: ReasonOnly, db: Session = Depends(get_db)):
    """
    Cancel an interview:
    - Mark interview as cancelled
    - Update application status also
    - Notify candidate & panel members
    """
    obj = db.query(Interview).filter(Interview.interview_id == interview_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Interview not found")

    obj.status = RoundStatus.cancelled
    obj.reason = payload.reason.strip() if payload.reason else None

    app = db.query(Application).filter(
        Application.candidate_id == obj.candidate_id,
        Application.job_id == obj.job_id
    ).first()

    if app:
        app.status = "interview_cancelled"

    db.commit()
    db.refresh(obj)

    subj, body = _cancelled_email_candidate(obj)
    _send_email_now([obj.candidate_email], subj, body)

    # In-app notification
    _notify_candidate_sync(
        obj.candidate_id,
        _notif_interview(obj, "cancelled")
    )

    for pm in obj.panel_members:
        if pm.get("email"):
            ps, pb = _cancelled_email_panel_member(obj, pm)
            _send_email_now([pm["email"]], ps, pb)

    return obj


@admin_interviews_router.patch(
    "/{interview_id}/result",
    response_model=dict,
    response_model_exclude_none=True
)
def update_interview_result(
    interview_id: int,
    result: InterviewUpdateResult,
    db: Session = Depends(get_db),
):
    """
    Update interview result:
    - HR sets passed/failed for this specific round
    - Update interview table
    - Update application table
    - Send email + in-app notification (with routing)
    """

    obj = (
        db.query(Interview)
        .filter(Interview.interview_id == interview_id)
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Interview not found")

    normalized = result.status.lower()
    if normalized not in {"passed", "failed"}:
        raise HTTPException(
            status_code=400,
            detail="Status must be passed/failed"
        )

    # -----------------------------
    # Update interview fields
    # -----------------------------
    obj.attended = result.attended
    obj.feedback_text = result.feedback_text
    obj.feedback_rating = result.feedback_rating
    obj.decision = result.decision
    obj.reason = result.reason
    obj.evaluated_by_name = result.evaluated_by_name or None
    obj.evaluated_by_email = result.evaluated_by_email or None
    obj.evaluated_at = datetime.utcnow()

    obj.status = (
        RoundStatus.passed
        if normalized == "passed"
        else RoundStatus.failed
    )

    # -----------------------------
    # Update application status
    # -----------------------------
    app = (
        db.query(Application)
        .filter(
            Application.candidate_id == obj.candidate_id,
            Application.job_id == obj.job_id
        )
        .first()
    )

    if app:
        app.status = normalized

    db.commit()
    db.refresh(obj)

    # -----------------------------
    # Send result email
    # -----------------------------
    subject, body, to = _result_email(obj, normalized)
    sent, err = _send_email_now(to, subject, body)

    # -----------------------------
    # In-app notification (UPDATED)
    # -----------------------------
    try:
        notif = NotificationStructure(
            scenario="interview_result_updated",
            message=f"Your interview result has been updated.",
            meta_data={
                # 🔥 EXACT ROUTE FROM YOUR SWAGGER SCREENSHOT
                "route_path": f"/interviews/{obj.interview_id}",
                "http_method": "GET",
            },
            data={
                "interview_id": obj.interview_id,
                "candidate_id": obj.candidate_id,
                "job_id": obj.job_id,
                "level_number": obj.level_number,
                "result": normalized,
                "updated_at_utc": datetime.utcnow().isoformat(),
            },
        )

        _notify_candidate_sync(obj.candidate_id, notif)

    except Exception as e:
        print("In-app notify error:", e)

    # -----------------------------
    # Response
    # -----------------------------
    return {
        "message": f"Interview marked {normalized}",
        "email_sent": sent,
        "email_error": err,
        "interview_id": obj.interview_id,
        "status": obj.status.value,
        "application_status": app.status if app else None,
        "updated_at_utc": datetime.utcnow().isoformat(),
    }



@admin_interviews_router.get("/interviews", response_model=List[InterviewOut])
def get_all_interviews(db: Session = Depends(get_db)):
    q = (
        db.query(Interview)
        .outerjoin(CandidateProfileInformation, CandidateProfileInformation.candidate_id == Interview.candidate_id)
        .outerjoin(Job, Job.job_id == Interview.job_id)
        .order_by(Interview.date.desc(), Interview.start_time.desc())
    )
    return q.all()


@admin_interviews_router.post("/{interview_id}/resend", response_model=dict)
def resend_interview_emails(
    interview_id: int,
    target: Literal["candidate", "panel", "both"] = Query("candidate"),
    db: Session = Depends(get_db),
):
    iv = (
        db.query(Interview)
        .filter(Interview.interview_id == interview_id)
        .first()
    )

    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found")

    TERMINAL_STATUSES = {
        RoundStatus.passed,
        RoundStatus.failed,
        RoundStatus.cancelled,
        RoundStatus.no_show,
    }

    if iv.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Resend not allowed. Interview already '{iv.status.value}'."
        )

    if target in ("panel", "both"):
        _ensure_per_panelist_tokens(db, iv)

    sent_to: list[str] = []
    errors: list[str] = []

    if target in ("candidate", "both"):
        subject, body = _scheduled_email_candidate(iv)
        ok, err = _send_email_now([iv.candidate_email], subject, body)
        if ok:
            sent_to.append("candidate")
        else:
            errors.append(f"candidate:{err}")

    if target in ("panel", "both"):
        for pm in iv.panel_members or []:
            email = pm.get("email")
            if not email:
                continue

            subject, body = _scheduled_email_panel_member(iv, pm)
            ok, err = _send_email_now([email], subject, body)
            if ok:
                sent_to.append(email)
            else:
                errors.append(f"{email}:{err}")

    return {
        "interview_id": interview_id,
        "current_status": iv.status.value,
        "resend_target": target,
        "sent_to": sent_to,
        "errors": errors,
        "resent_at_utc": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Applications Section
# =============================================================================


def _to_row(a: Application, c: CandidateProfileInformation, j: Job) -> AdminApplicationRowOut:
    return AdminApplicationRowOut(
        application_id=a.application_id,
        candidate_id=c.candidate_id,
        candidate_name=f"{c.first_name} {c.last_name}",
        candidate_email=c.email,
        job_id=j.job_id,
        job_title=j.title,
        job_location=j.location,
        applied_date=a.applied_at,
        status=a.status,
        source=a.source.value if hasattr(a.source, "value") else str(a.source),
        is_referral=(a.source == ApplicationSource.internal_employee),
    )


admin_applications_router = APIRouter(
    prefix="/admin/applications",
    tags=["Admin - Applications"],
    dependencies=[Depends(require_hr_or_superadmin), Depends(hr_session_required)],
)


@admin_applications_router.get("/meta")
def applications_meta(db: Session = Depends(get_db)):
    jobs = db.query(Job).filter(Job.status == "Open").order_by(Job.title.asc()).all()
    return {
        "jobs": [{"job_id": j.job_id, "title": j.title} for j in jobs],
        "statuses": [
            "under_review", "shortlisted", "rejected",
            "interview_scheduled", "interview_passed", "interview_failed",
            "offer_made", "offer_accepted", "offer_rejected", "hired", "withdrawn",
        ],
    }


@admin_applications_router.get("/page", response_model=AdminApplicationsPageOut)
def admin_applications_page(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    application_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = (
        db.query(Application, CandidateProfileInformation, Job)
        .join(CandidateProfileInformation, CandidateProfileInformation.candidate_id == Application.candidate_id)
        .join(Job, Job.job_id == Application.job_id)
        .order_by(desc(Application.applied_at))
    )

    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                CandidateProfileInformation.first_name.ilike(like),
                CandidateProfileInformation.last_name.ilike(like),
                CandidateProfileInformation.email.ilike(like),
                Job.title.ilike(like),
            )
        )

    if status and status.lower() != "all":
        q = q.filter(Application.status == status)

    if job_id and job_id.lower() != "all":
        q = q.filter(Application.job_id == job_id)

    if application_id:
        q = q.filter(Application.application_id == application_id)

    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    items = [_to_row(a, c, j) for (a, c, j) in rows]

    return AdminApplicationsPageOut(items=items, total=total, limit=limit, offset=offset)


@admin_applications_router.get("/export")
def admin_applications_export_to_s3(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    application_id: Optional[str] = Query(None),
):
    q = (
        db.query(Application, CandidateProfileInformation, Job)
        .join(CandidateProfileInformation, CandidateProfileInformation.candidate_id == Application.candidate_id)
        .join(Job, Job.job_id == Application.job_id)
        .order_by(desc(Application.applied_at))
    )

    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                CandidateProfileInformation.first_name.ilike(like),
                CandidateProfileInformation.last_name.ilike(like),
                CandidateProfileInformation.email.ilike(like),
                Job.title.ilike(like),
            )
        )

    if status and status.lower() != "all":
        q = q.filter(Application.status == status)

    if job_id and job_id.lower() != "all":
        q = q.filter(Application.job_id == job_id)

    if application_id:
        q = q.filter(Application.application_id == application_id)

    rows = q.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No applications found")

    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"

    headers = [
        "Application ID", "Applied At (UTC)", "Status", "Source", "Is Referral",
        "Candidate ID", "Candidate Name", "Email", "Contact Number",
        "Gender", "DOB", "Highest Qualification",
        "Total Exp", "Relevant Exp", "Last Company", "Last Designation",
        "Primary Skills", "Secondary Skills",
        "Job ID", "Job Title", "Job Type", "Work Mode",
        "Location", "Experience Required",
    ]
    ws.append(headers)

    for a, c, j in rows:
        primary = secondary = ""
        if c.skills:
            data = json.loads(c.skills) if isinstance(c.skills, str) else c.skills
            primary = ", ".join(data.get("primary_skills", []))
            secondary = ", ".join(data.get("secondary_skills", []))

        last_company = last_designation = ""
        if c.experience:
            exp = json.loads(c.experience) if isinstance(c.experience, str) else c.experience
            if isinstance(exp, dict) and exp:
                last = list(exp.values())[-1]
                last_company = last.get("company_name", "")
                last_designation = last.get("designation", "")

        ws.append([
            a.application_id,
            a.applied_at.isoformat() if a.applied_at else "",
            a.status,
            a.source.value if hasattr(a.source, "value") else str(a.source),
            "Yes" if a.source == ApplicationSource.internal_employee else "No",
            c.candidate_id,
            f"{c.first_name} {c.last_name}".strip(),
            c.email,
            c.contact_number,
            c.gender,
            c.date_of_birth,
            c.highest_qualification,
            c.total_experience,
            c.relevant_experience,
            last_company,
            last_designation,
            primary,
            secondary,
            j.job_id,
            j.title,
            j.job_type,
            j.work_mode,
            j.location,
            j.total_experience,
        ])

    for col in ws.columns:
        ws.column_dimensions[get_column_letter(col[0].column)].width = 22

    excel_buffer = io.BytesIO()
    wb.save(excel_buffer)
    excel_buffer.seek(0)

    filename = f"applications_export_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.xlsx"
    s3_key = f"exports/applications/{filename}"

    s3_uri = put_bytes(
        data=excel_buffer.getvalue(),
        key=s3_key,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # Convert s3:// → public HTTPS URL
    public_url = f"https://{os.getenv('S3_BUCKET')}.s3.{os.getenv('AWS_REGION', 'ap-south-1')}.amazonaws.com/{s3_key}"

    # --------------------------------------------------
    # Return public URL
    # --------------------------------------------------
    return {
        "message": "Applications exported successfully",
        "file_name": filename,
        "s3_uri": s3_uri,          # internal reference
        "download_url": public_url # PUBLIC URL (no expiry)
    }


@admin_applications_router.get("/ids")
def admin_application_ids(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    application_id: Optional[str] = Query(None),
):
    """
    Get only application_ids based on filters
    """

    q = (
        db.query(Application.application_id)
        .join(
            CandidateProfileInformation,
            CandidateProfileInformation.candidate_id == Application.candidate_id
        )
        .join(Job, Job.job_id == Application.job_id)
        .order_by(desc(Application.applied_at))
    )

    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                CandidateProfileInformation.first_name.ilike(like),
                CandidateProfileInformation.last_name.ilike(like),
                CandidateProfileInformation.email.ilike(like),
                Job.title.ilike(like),
            )
        )

    if status and status.lower() != "all":
        q = q.filter(Application.status == status)

    if job_id and job_id.lower() != "all":
        q = q.filter(Application.job_id == job_id)

    if application_id:
        q = q.filter(Application.application_id == application_id)

    rows = q.all()

    if not rows:
        return {
            "count": 0,
            "application_ids": []
        }

    # rows = [(id1,), (id2,), ...]
    application_ids = [row[0] for row in rows]

    return {
        "count": len(application_ids),
        "application_ids": application_ids
    }


@admin_applications_router.get("/{application_id}", response_model=AdminApplicationDetailOut)
def admin_application_detail(application_id: str, db: Session = Depends(get_db)):
    a = db.query(Application).filter(Application.application_id == application_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")

    c = a.candidate
    j = a.job
    if not c or not j:
        raise HTTPException(status_code=404, detail="Related candidate or job not found")

    return AdminApplicationDetailOut(
        application_id=a.application_id,
        status=a.status,
        source=a.source.value if hasattr(a.source, "value") else str(a.source),
        is_referral=(a.source == ApplicationSource.internal_employee),
        applied_at=a.applied_at,
        candidate_id=c.candidate_id,
        candidate_name=f"{c.first_name} {c.last_name}",
        candidate_email=c.email,
        contact_number=c.contact_number,
        job_id=j.job_id,
        job_title=j.title,
        job_location=j.location,
        work_mode=j.work_mode,
        total_experience=j.total_experience,
    )


# Resume Download Helpers
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("AWS_S3_BUCKET")
MEDIA_ROOT = os.getenv("MEDIA_ROOT", "/app/media")


def _looks_like_s3_url(url: str) -> bool:
    if not url:
        return False
    return url.startswith("s3://") or (url.startswith(("http://", "https://")) and "amazonaws.com" in url)


def _bucket_key_from_s3_url(url: str):
    p = urlparse(url)
    if p.scheme == "s3":
        return p.netloc, unquote(p.path.lstrip("/"))
    path = unquote(p.path.lstrip("/"))
    parts = p.netloc.split(".")
    if parts and parts[0] != "s3":
        return parts[0], path
    bucket, key = path.split("/", 1)
    return bucket, key


def _stream_s3_object(bucket: str, key: str, filename: str):
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials missing")
    except ClientError:
        raise HTTPException(status_code=404, detail="Resume not found")
    except Exception:
        raise HTTPException(status_code=500, detail="S3 fetch failed")

    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return StreamingResponse(
        obj["Body"],
        media_type=ctype,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=0, no-cache",
        },
    )

# =============================================================================
# Admin: Download resume by application_id (Frontend-safe & future-proof)
# =============================================================================
@admin_applications_router.get("/{application_id}/resume")
def admin_application_resume(
    application_id: str,
    db: Session = Depends(get_db),
):
    application = (
        db.query(Application)
        .filter(Application.application_id == application_id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    resume = (
        db.query(CandidateDocument)
        .filter(
            CandidateDocument.candidate_id == application.candidate_id,
            func.lower(CandidateDocument.doc_name) == "resume",
        )
        .order_by(CandidateDocument.uploaded_at.desc())
        .first()
    )

    if not resume or not resume.file_path:
        raise HTTPException(status_code=404, detail="Resume document not found")

    try:
        download_url = generate_http_url(
            resume.file_path
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate resume download URL: {str(e)}"
        )

    return {
        "application_id": application.application_id,
        "candidate_id": application.candidate_id,
        "file_name": resume.file_name or "resume.pdf",
        "download_url": download_url,
    }


@admin_applications_router.get("/{application_id}/screening-basic")
def admin_application_screening_basic(
    application_id: str,
    db: Session = Depends(get_db),
):
    # Fetch application
    application = (
        db.query(Application)
        .filter(Application.application_id == application_id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    candidate = application.candidate
    job = application.job

    if not candidate or not job:
        raise HTTPException(status_code=404, detail="Candidate or Job not found")

    # Fetch screening entry
    screening = (
        db.query(ResumeScreening)
        .filter(ResumeScreening.application_id == application_id)
        .first()
    )
    if not screening:
        raise HTTPException(status_code=404, detail="Screening not found")

    # Prepare response (minimal details)
    return {
        "application_id": application.application_id,
        "candidate_id": candidate.candidate_id,
        "candidate_name": f"{candidate.first_name} {candidate.last_name}",
        "candidate_email": candidate.email,
        "job_id": job.job_id,
        "job_title": job.title,
        "screening_status": screening.status,
        "final_score": round(screening.score or 0, 1),
        "matched_skills": (
            screening.matched_skills.split(",") if screening.matched_skills else []
        ),
        "missing_skills": (
            screening.missing_skills.split(",") if screening.missing_skills else []
        ),
        "experience_match": screening.experience_match,
        "updated_at": screening.updated_at or screening.created_at,
    }


def _application_status_email(cand, job, new_status):
    title = new_status.replace("_", " ").title()
    subject = f"Application Status Updated — Job {job.job_id} ({job.title})"
    body = (
        f"Dear {cand.first_name},\n\n"
        f"Your application status for Job {job.job_id} — {job.title} has been updated to {title}.\n\n"
        f"Location: {job.location}\nWork Mode: {job.work_mode}\n\nRegards,\nRecruitment Team"
    )
    return subject, body, [cand.email]

@admin_applications_router.patch("/applications/{application_id}/status")
def update_application_status(
    application_id: str,
    body: ApplicationStatusUpdate,
    db: Session = Depends(get_db),
):
    app_row = (
        db.query(Application)
        .filter(Application.application_id == application_id)
        .first()
    )
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")

    # -----------------------------
    # HR sets new status
    # -----------------------------
    new_status = body.status.lower()
    app_row.status = new_status
    db.commit()

    # -----------------------------
    # Fetch candidate + job
    # -----------------------------
    candidate = (
        db.query(CandidateProfileInformation)
        .filter(CandidateProfileInformation.candidate_id == app_row.candidate_id)
        .first()
    )

    job = (
        db.query(Job)
        .filter(Job.job_id == app_row.job_id)
        .first()
    )

    if candidate and job:
        # -----------------------------
        # Send Email
        # -----------------------------
        subject, body_text, to = _application_status_email(
            candidate, job, new_status
        )
        _send_email_now(to, subject, body_text)

        # -----------------------------
        # In-App Notification (UPDATED)
        # -----------------------------
        notif = NotificationStructure(
            scenario="application_status_updated",
            message=f"Your application status has been updated to {new_status}.",
            meta_data={
                # ✅ ROUTE FROM YOUR SWAGGER SCREENSHOT
                "route_path": "/candidate/profile/applications/me",
                "http_method": "GET",
                "query_params": {
                    "limit": 50,
                    "offset": 0,
                },
            },
            data={
                "application_id": application_id,
                "job_id": job.job_id,
                "job_title": job.title,
                "new_status": new_status,
                "updated_at_utc": datetime.utcnow().isoformat(),
            },
        )

        _notify_candidate_sync(candidate.candidate_id, notif)

    return {
        "application_id": application_id,
        "status": new_status,
    }


# Export Routers
# =============================================================================
__all__ = ["admin_interviews_router", "admin_applications_router"]
#=============================================================================


