# app/routers/jobs.py
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Dict, Optional, Tuple, Iterable, Any
from sqlalchemy import text, event, desc, func
from io import StringIO, BytesIO
import pandas as pd
import os
import asyncio
from app.schemas.user_schemas import NotificationMeta, NotificationStructure
from app.utils.aws_email import aws_send_mail
from app.database import get_db, SessionLocal
from app.utils.notify_depends import send_notification_to_candidate
from app.models.job_models import Job, Application, Company
from app.models.user_models import CandidateProfileInformation
from app.models.screening_models import ResumeScreening
from app.schemas.jobs_schemas import (
    JobCreate,
    JobUpdate,
    JobOut,
    JobApplicationItem,
    JobApplicationList,
    JobMetricsOut,
    JobDetailOut,
)
# from app.utils.dependencies_utils import sendgrid_send_mail
from app.utils.guards import hr_session_required

# ===============================================================
# Router Protection
# ===============================================================
router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"],
    dependencies=[Depends(hr_session_required)],
)

# ===============================================================
# Helper Functions
# ===============================================================
def _normalize_csv_or_iter(v: Optional[Any]) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return sorted(set([p.strip().lower() for p in v.split(",") if p.strip()]))
    if isinstance(v, Iterable) and not isinstance(v, (bytes, bytearray, dict)):
        return sorted(set([str(x).strip().lower() for x in v if x]))
    s = str(v).strip()
    return [s.lower()] if s else []


def _job_skills(job: Job) -> List[str]:
    return sorted(set(
        _normalize_csv_or_iter(job.primary_skills) +
        _normalize_csv_or_iter(job.secondary_skills)
    ))


def _candidate_registration_skills(cand: CandidateProfileInformation) -> List[str]:
    skills_json = cand.skills or {}
    if not isinstance(skills_json, dict):
        return _normalize_csv_or_iter(skills_json)

    primary = _normalize_csv_or_iter(
        skills_json.get("primary_skills") or skills_json.get("primary")
    )
    secondary = _normalize_csv_or_iter(
        skills_json.get("secondary_skills") or skills_json.get("secondary")
    )
    return sorted(set(primary + secondary))


def _overlap(job_skills: List[str], cand_skills: List[str]) -> int:
    return len(set(job_skills) & set(cand_skills))


def _job_url(job_id: str) -> Optional[str]:
    tpl = os.getenv("FRONTEND_JOB_URL_TEMPLATE")
    return tpl.replace("{job_id}", job_id) if tpl else None

# ===============================================================
# Email Helpers
# ===============================================================
def _build_job_alert_email(
    cand: CandidateProfileInformation,
    job: Job
) -> Tuple[str, str]:
    subject = f"New role that matches your skills: {job.title} (Job {job.job_id})"
    hi = (cand.first_name or "").strip() or "there"
    apply_link = _job_url(job.job_id)

    lines = [
        f"Hi {hi},",
        "",
        "We just posted a new role that matches your profile:",
        f"• Title: {job.title}",
        f"• Job ID: {job.job_id}",
        f"• Location: {job.location}",
        f"• Work Mode: {job.work_mode}",
        f"• Primary Skills: {job.primary_skills or ''}",
    ]

    if job.secondary_skills:
        lines.append(f"• Secondary Skills: {job.secondary_skills}")

    if job.total_experience or job.relevant_experience:
        exp = []
        if job.total_experience:
            exp.append(job.total_experience)
        if job.relevant_experience:
            exp.append(f"Relevant: {job.relevant_experience}")
        lines.append("• Experience: " + " | ".join(exp))

    if apply_link:
        lines.append("")
        lines.append(f"Apply now: {apply_link}")

    lines += ["", "Regards,", "Talent Acquisition Team"]

    return subject, "\n".join(lines)


# ---------------------------------------------------------------
# AWS SES Email Credentials
# ---------------------------------------------------------------
def _email_creds() -> dict:
    return {
        "FROM_EMAIL": os.getenv("SES_SENDER", "careers@securxperts.com"),
    }

_email_creds()


# ---------------------------------------------------------------
# Fire-and-forget async execution
# ---------------------------------------------------------------
def _fire_and_forget(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(coro)
        else:
            loop.run_until_complete(coro)
    except RuntimeError:
        asyncio.run(coro)


# ---------------------------------------------------------------
# Send Job Alert Email (via AWS SES helper)
# ---------------------------------------------------------------
def _send_job_email_via_helper(to_email: str, subject: str, body: str):
    creds = _email_creds()

    coro = aws_send_mail(
        from_email=creds["FROM_EMAIL"],
        reciver_to=[to_email],
        subject=subject,
        body=body,
        body_type="plain",
    )

    _fire_and_forget(coro)

# ======================================================================
# notification helper
# =======================================================================

def _notify_candidate_job_match(candidate_id: str, notif: NotificationStructure) -> None:
    if not candidate_id or not notif:
        return

    db = SessionLocal()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(
                send_notification_to_candidate(candidate_id, notif, db)
            )
        else:
            loop.run_until_complete(
                send_notification_to_candidate(candidate_id, notif, db)
            )
    except RuntimeError:
        asyncio.run(
            send_notification_to_candidate(candidate_id, notif, db)
        )
    except Exception as e:
        db.rollback()
        print(f"[Candidate Job Notification Failed] {candidate_id}: {e}")
    finally:
        db.close()

# ==============================================================
# duplicate job helper
# ===============================================================

def _job_exists(db: Session, employee_id: str, job) -> bool:
    return db.query(Job).filter(
        Job.company_id == job.company_id,
        Job.posted_by_employee_id == employee_id,
        Job.title == job.title,
        Job.job_type == job.job_type,
        Job.description == job.description,
        Job.work_mode == job.work_mode,
        Job.responsibilities == job.responsibilities,
        Job.primary_skills == job.primary_skills,
        Job.secondary_skills == job.secondary_skills,
        Job.qualification_requirement == job.qualification_requirement,
        Job.total_experience == job.total_experience,
        Job.relevant_experience == job.relevant_experience,
        Job.salary == job.salary,
        Job.location == job.location,
        Job.designation == job.designation,
        Job.band == job.band,
        Job.category == job.category,
        Job.application_deadline == job.application_deadline,
        Job.status == (job.status or "Open"),
    ).first() is not None


# ===============================================================
# Background Notify — FINAL VERSION (Email + In-App Notification)
# ===============================================================
def notify_candidates_for_new_job(job_id: str, min_overlap=1, max_recipients=200):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.job_id == job_id, Job.status == "Open").first()
        if not job:
            return

        jskills = _job_skills(job)
        if not jskills:
            return

        already_applied = {
            a.candidate_id
            for a in db.query(Application).filter(Application.job_id == job.job_id)
        }

        candidates = db.query(CandidateProfileInformation).all()

        scored = []
        for cand in candidates:
            if cand.candidate_id in already_applied:
                continue

            overlap = _overlap(jskills, _candidate_registration_skills(cand))
            if overlap >= min_overlap:
                scored.append((overlap, cand))

        scored.sort(key=lambda t: t[0], reverse=True)
        selected = [cand for _, cand in scored[:max_recipients]]

        for cand in selected:
            # ---------------- EMAIL ----------------
            if cand.email:
                subject, body = _build_job_alert_email(cand, job)
                _send_job_email_via_helper(cand.email, subject, body)

            # ---------------- IN-APP NOTIFICATION ----------------
            notif = NotificationStructure(
                scenario="job_matched",
                meta_data=NotificationMeta(
                    routh_path="/candidate/profile/jobs/matched",
                    http_method="GET",
                ),
                message=(
                    f"New job matching your profile: "
                    f"{job.title} ({job.job_id})"
                ),
                data={
                    "job_id": job.job_id,
                    "job_title": job.title,
                    "location": job.location,
                    "work_mode": job.work_mode,
                },
                timestamp=datetime.now().__str__()
            )

            _notify_candidate_job_match(
                cand.candidate_id,
                notif
            )

    except Exception as e:
        print(f"[notify_candidates_for_new_job] ERROR: {e}")
    finally:
        db.close()

# ===============================================================
# Job ID Auto-generation
# ===============================================================
@event.listens_for(Job, "before_insert")
def generate_job_id(mapper, connection, target):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        sql = "SELECT MAX(CAST(substr(job_id, 2) AS INTEGER)) FROM jobs"
    elif dialect in ("postgresql", "postgres"):
        sql = "SELECT MAX(CAST(SUBSTRING(job_id, 2) AS INTEGER)) FROM jobs"
    else:
        sql = "SELECT MAX(CAST(SUBSTRING(job_id, 2) AS UNSIGNED)) FROM jobs"

    try:
        res = connection.execute(text(sql))
        max_num = res.scalar() or 0
    except:
        res = connection.execute(text("SELECT job_id FROM jobs ORDER BY job_id DESC LIMIT 1"))
        last = res.scalar()
        try:
            max_num = int((last or "J000")[1:])
        except:
            max_num = 0

    target.job_id = f"J{max_num + 1:03d}"

# ===============================================================
# CREATE JOB (with company_id, category, number_of_positions)
# ===============================================================
@router.post("/create", response_model=Dict)
def create_job(
    job: JobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    employee_id: str = Depends(hr_session_required),
):
    comp = db.query(Company).filter(Company.company_id == job.company_id).first()
    if not comp:
        raise HTTPException(400, "Invalid company_id")

    # DUPLICATE CHECK
    if _job_exists(db, employee_id, job):
        raise HTTPException(
            status_code=409,
            detail="Duplicate job already exists with the same details"
        )

    new_job = Job(
        posted_by_employee_id=employee_id,
        company_id=job.company_id,
        title=job.title,
        job_type=job.job_type,
        description=job.description,
        work_mode=job.work_mode,
        responsibilities=job.responsibilities,
        primary_skills=job.primary_skills,
        secondary_skills=job.secondary_skills,
        qualification_requirement=job.qualification_requirement,
        salary=job.salary,
        total_experience=job.total_experience,
        relevant_experience=job.relevant_experience,
        location=job.location,
        designation=job.designation,
        band=job.band,
        number_of_positions=job.number_of_positions,
        category=job.category,
        application_deadline=job.application_deadline,
        status=job.status or "Open",
    )

    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    background_tasks.add_task(notify_candidates_for_new_job, new_job.job_id)

    return {
        "message": "Job created successfully",
        "job_id": new_job.job_id
    }


# ===============================================================
# BULK JOB UPLOAD (duplicate-safe)
# ===============================================================
@router.post("/upload-jobs")
async def upload_multiple_jobs(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    employee_id: str = Depends(hr_session_required),
):

    filename = file.filename.lower()
    if not (filename.endswith(".csv") or filename.endswith(".xlsx")):
        raise HTTPException(400, "Upload CSV or Excel only")

    try:
        content = await file.read()
        if filename.endswith(".csv"):
            df = pd.read_csv(StringIO(content.decode("utf-8")))
        else:
            df = pd.read_excel(BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"Error reading file: {e}")

    required = [
        "company_id", "title", "job_type", "description", "work_mode",
        "responsibilities", "primary_skills", "qualification_requirement",
        "total_experience", "location", "designation", "band"
    ]

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise HTTPException(400, f"Missing required columns: {', '.join(missing)}")

    created: list[Job] = []
    failed: list[dict] = []

    for idx, row in df.iterrows():
        try:
            # ---------------- Validate company ----------------
            comp = db.query(Company).filter(
                Company.company_id == row["company_id"]
            ).first()
            if not comp:
                raise ValueError("Invalid company_id")

            # ---------------- Duplicate check ----------------
            duplicate = db.query(Job).filter(
                Job.posted_by_employee_id == employee_id,
                Job.company_id == row["company_id"],
                Job.title == row["title"],
                Job.job_type == row["job_type"],
                Job.description == row["description"],
                Job.work_mode == row["work_mode"],
                Job.responsibilities == row["responsibilities"],
                Job.primary_skills == row["primary_skills"],
                Job.secondary_skills == row.get("secondary_skills"),
                Job.qualification_requirement == row["qualification_requirement"],
                Job.total_experience == row["total_experience"],
                Job.relevant_experience == row.get("relevant_experience"),
                Job.salary == row.get("salary"),
                Job.location == row["location"],
                Job.designation == row["designation"],
                Job.band == row["band"],
                Job.category == row.get("category"),
                Job.application_deadline ==
                (
                    pd.to_datetime(row.get("application_deadline")).date()
                    if row.get("application_deadline") else None
                ),
                Job.status == row.get("status", "Open"),
            ).first()

            if duplicate:
                failed.append({
                    "row": idx + 1,
                    "error": "Duplicate job already exists"
                })
                continue

            # ---------------- Create job ----------------
            new_job = Job(
                posted_by_employee_id=employee_id,
                company_id=row["company_id"],
                title=row["title"],
                job_type=row["job_type"],
                description=row["description"],
                work_mode=row["work_mode"],
                responsibilities=row["responsibilities"],
                primary_skills=row["primary_skills"],
                secondary_skills=row.get("secondary_skills"),
                qualification_requirement=row["qualification_requirement"],
                total_experience=row["total_experience"],
                relevant_experience=row.get("relevant_experience"),
                salary=row.get("salary"),
                location=row["location"],
                designation=row["designation"],
                band=row["band"],
                number_of_positions=int(row.get("number_of_positions", 1)),
                category=row.get("category"),
                application_deadline=pd.to_datetime(row.get("application_deadline")).date()
                if row.get("application_deadline") else None,
                status=row.get("status", "Open"),
            )

            db.add(new_job)
            db.flush()   # keep transaction alive
            created.append(new_job)

        except Exception as e:
            db.rollback()
            failed.append({
                "row": idx + 1,
                "error": str(e)
            })

    db.commit()

    if background_tasks:
        for job in created:
            background_tasks.add_task(
                notify_candidates_for_new_job,
                job.job_id
            )

    return {
        "created": len(created),
        "failed": failed
    }

# ===============================================================
# UPDATE JOB
# ===============================================================
@router.put("/update/{job_id}", response_model=Dict)
def update_job(job_id: str, payload: JobUpdate, db: Session = Depends(get_db)):

    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    data = payload.dict(exclude_unset=True)

    if "company_id" in data:
        comp = db.query(Company).filter(Company.company_id == data["company_id"]).first()
        if not comp:
            raise HTTPException(400, "Invalid company_id")

    for key, val in data.items():
        setattr(job, key, val)

    db.commit()
    db.refresh(job)

    return {"message": "Job updated successfully", "updated_fields": data}

# ===============================================================
# GET ALL JOBS
# ===============================================================
@router.get("/all", response_model=List[JobOut])
def get_all_jobs(db: Session = Depends(get_db)):
    return db.query(Job).filter(Job.status == "Open").all()

# ===============================================================
# APPLICATION LIST
# ===============================================================
@router.get("/{job_id}/applications", response_model=JobApplicationList)
def list_job_applications(job_id: str, status: Optional[str] = None,
                          limit: int = 50, offset: int = 0,
                          db: Session = Depends(get_db)):

    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    q = db.query(Application).filter(Application.job_id == job_id).order_by(desc(Application.applied_at))
    if status:
        q = q.filter(Application.status == status.lower())

    total = q.count()
    apps = q.offset(offset).limit(limit).all()

    items = []
    for a in apps:
        cand = db.query(CandidateProfileInformation).filter_by(candidate_id=a.candidate_id).first()
        scr = db.query(ResumeScreening).filter_by(application_id=a.application_id).first()

        items.append(
            JobApplicationItem(
                application_id=a.application_id,
                candidate_id=a.candidate_id,
                name=" ".join(filter(None, [cand.first_name if cand else "", cand.last_name if cand else ""])),
                email=cand.email if cand else "",
                status=(a.status or "").lower(),
                screening_status=scr.status if scr else None,
                screening_score=scr.score if scr else None,
            )
        )

    return JobApplicationList(job_id=job_id, total=total, items=items)

# ===============================================================
# CLOSE JOB
# ===============================================================
@router.put("/close/{job_id}")
def close_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = "Closed"
    db.commit()
    return {"message": f"Job {job_id} closed"}

# ===============================================================
# DELETE JOB
# ===============================================================
@router.delete("/delete/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    db.delete(job)
    db.commit()
    return {"message": f"Job {job_id} deleted"}

# ===============================================================
# JOB DETAIL VIEW
# ===============================================================
@router.get("/{job_id}", response_model=JobDetailOut)
def get_job_detail(job_id: str, db: Session = Depends(get_db)):

    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    rows = (
        db.query(Application.status, func.count())
        .filter(Application.job_id == job_id)
        .group_by(Application.status)
        .all()
    )

    by_status = {status: count for status, count in rows}

    last_applied = (
        db.query(func.max(Application.applied_at))
        .filter(Application.job_id == job_id)
        .scalar()
    )

    job_out = JobOut.model_validate(job)

    metrics = JobMetricsOut(
        total_applicants=sum(by_status.values()),
        by_status=by_status,
        last_applied_at=last_applied,
    )
    return JobDetailOut(job=job_out, metrics=metrics)
