# app/routers/panel_feedback.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import desc
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime
import json
import asyncio
import logging

from app.database import get_db
from app.utils.guards import hr_session_required
from app.models.job_models import Interview, InterviewFeedback
from app.schemas.feedback_schemas import (
    PanelFeedbackCreate,
    PanelFeedbackOut,
    PanelFeedbackListOut,
)
from app.schemas.user_schemas import NotificationStructure, NotificationMeta
from app.utils.notify_depends import send_notification_to_employee as send_notification

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

panel_feedback_router = APIRouter(
    prefix="/panel/feedback",
    tags=["Panel Feedback"],
)

# ===============================================================
# Notification Helper (SAFE)
# ===============================================================
def _notify_hr_panel_feedback_sync(employee_id: str, payload: dict) -> None:
    if not employee_id:
        return
    try:
        asyncio.run(send_notification(employee_id, payload))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(send_notification(employee_id, payload))
    except Exception as e:
        logger.error(f"[PanelFeedback Notification Error] {e}")

# ===============================================================
# Submit Panel Feedback (Single-Use Token)
# ===============================================================
@panel_feedback_router.post(
    "/submit/{feedback_token}",
    response_model=PanelFeedbackOut,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
def submit_panel_feedback(
    payload: PanelFeedbackCreate,
    feedback_token: str,
    db: Session = Depends(get_db),
):
    """
    Secure single-use panel feedback submission.
    """

    try:
        interview = (
            db.query(Interview)
            .filter(Interview.interview_id == payload.interview_id)
            .with_for_update()
            .first()
        )
    except OperationalError:
        raise HTTPException(
            status_code=409,
            detail="Interview is being updated. Please try again shortly.",
        )

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # --------------------------------------------------
    # Parse panel members safely
    # --------------------------------------------------
    panel_members = interview.panel_members or []
    if isinstance(panel_members, str):
        try:
            panel_members = json.loads(panel_members)
        except Exception:
            panel_members = []

    matched_index = None
    matched_member = None
    token_str = str(feedback_token)

    for idx, member in enumerate(panel_members):
        if (
            isinstance(member, dict)
            and str(member.get("feedback_token")) == token_str
        ):
            matched_index = idx
            matched_member = member
            break

    if not matched_member:
        raise HTTPException(status_code=403, detail="Invalid or expired feedback token")

    submitted_email = (payload.panel_member_email or "").lower().strip()
    assigned_email = (matched_member.get("email") or "").lower().strip()

    if submitted_email != assigned_email:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned panel member can submit this feedback.",
        )

    if matched_member.get("submitted") or not matched_member.get("feedback_token"):
        raise HTTPException(
            status_code=410,
            detail="This feedback link has already been used.",
        )

    # Prevent duplicate feedback
    exists = (
        db.query(InterviewFeedback.feedback_id)
        .filter(
            InterviewFeedback.interview_id == payload.interview_id,
            InterviewFeedback.panel_member_email == submitted_email,
        )
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=410,
            detail="Feedback already submitted for this panelist.",
        )

    # --------------------------------------------------
    # Save feedback
    # --------------------------------------------------
    try:
        feedback = InterviewFeedback(
            interview_id=payload.interview_id,
            panel_member_name=payload.panel_member_name,
            panel_member_email=submitted_email,
            attended=bool(payload.attended),
            questions_asked=payload.questions_asked,
            feedback_text=payload.feedback_text,
            feedback_rating=payload.feedback_rating,
            decision=payload.decision,
            reason=payload.reason,
            approved=False,
        )
        db.add(feedback)

        updated_member = dict(matched_member)
        updated_member.update({
            "submitted": True,
            "submitted_at": datetime.utcnow().isoformat(),
            "feedback_token": None,
        })

        panel_members[matched_index] = updated_member
        interview.panel_members = panel_members
        flag_modified(interview, "panel_members")

        db.commit()
        db.refresh(feedback)

        # --------------------------------------------------
        # Notify HR (FINAL & CORRECT)
        # --------------------------------------------------
        job = getattr(interview, "job", None)
        hr_employee_id = getattr(job, "posted_by_employee_id", None) if job else None

        if hr_employee_id:
            level_label = interview.round_name or f"L{interview.level_number}"

            notif = NotificationStructure(
                scenario="panel_feedback_submitted",
                meta_data=NotificationMeta(
                    routh_path=(
                        f"/panel/feedback/interview/{interview.interview_id}"
                        f"?limit=50&offset=0"
                    ),
                    http_method="GET",
                ),
                message=(
                    f"Panel feedback submitted for "
                    f"{interview.candidate_first_name} {interview.candidate_last_name} "
                    f"— {level_label} (Job {interview.job_id})"
                ),
            )

            _notify_hr_panel_feedback_sync(
                hr_employee_id,
                notif.model_dump()  # use .dict() if pydantic v1
            )

        return feedback

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while submitting feedback.",
        )
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unexpected server error.",
        )


# ===============================================================
# List Panel Feedback (HR only, PAGINATED)
# ===============================================================
@panel_feedback_router.get(
    "/interview/{interview_id}",
    response_model=PanelFeedbackListOut,
    dependencies=[Depends(hr_session_required)],
)
def list_panel_feedback_for_interview(
    interview_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    interview = (
        db.query(Interview)
        .filter(Interview.interview_id == interview_id)
        .first()
    )
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    query = (
        db.query(InterviewFeedback)
        .filter(InterviewFeedback.interview_id == interview_id)
        .order_by(desc(InterviewFeedback.submitted_at))
    )

    total = query.count()
    items = query.offset(offset).limit(limit).all()

    return PanelFeedbackListOut(items=items, total=total)
