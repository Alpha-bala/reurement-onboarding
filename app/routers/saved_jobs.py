from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
import uuid
import logging

from app.database import get_db
from app.models.job_models import SavedJob, Job
from app.models.user_models import CandidateProfileInformation
from app.schemas.jobs_schemas import SavedJobCreate, SavedJobOut
from app.utils.guards import candidate_session_required

logger = logging.getLogger("app.routers.saved_jobs")
logger.setLevel(logging.INFO)

# ===============================================================
# Router: Candidate-only + Active Session
# ===============================================================
router = APIRouter(
    prefix="/saved-jobs",
    tags=["Saved Jobs"],
    dependencies=[Depends(candidate_session_required)],
)

# ===============================================================
# Save a Job
# ===============================================================
@router.post("/save")
def save_job(
    data: SavedJobCreate,
    candidate_id: str = Depends(candidate_session_required),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter(Job.job_id == data.job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    candidate = (
        db.query(CandidateProfileInformation)
        .filter(CandidateProfileInformation.candidate_id == candidate_id)
        .first()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    existing = (
        db.query(SavedJob)
        .filter(
            SavedJob.candidate_id == candidate_id,
            SavedJob.job_id == data.job_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Job already saved")

    try:
        saved = SavedJob(
            id=str(uuid.uuid4()),
            candidate_id=candidate_id,
            job_id=data.job_id,
            saved_on=datetime.utcnow(),
        )
        db.add(saved)
        db.commit()
        db.refresh(saved)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save job")

    logger.info(f"[SavedJob] Candidate={candidate_id} saved job={data.job_id}")

    return {
        "message": "Job saved successfully",
        "job_id": data.job_id,
        "candidate_id": candidate_id,
    }


# ===============================================================
# Unsave (Remove) a Job
# ===============================================================
@router.delete("/unsave")
def unsave_job(
    data: SavedJobCreate,
    candidate_id: str = Depends(candidate_session_required),
    db: Session = Depends(get_db),
):
    saved = (
        db.query(SavedJob)
        .filter(
            SavedJob.candidate_id == candidate_id,
            SavedJob.job_id == data.job_id,
        )
        .first()
    )

    if not saved:
        raise HTTPException(status_code=404, detail="Saved job not found")

    try:
        db.delete(saved)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unsave job")

    logger.info(f"[SavedJob] Candidate={candidate_id} unsaved job={data.job_id}")

    return {
        "message": "Job unsaved successfully",
        "job_id": data.job_id,
        "candidate_id": candidate_id,
    }


# ===============================================================
# List All Saved Jobs for a Candidate (PAGINATED)
# ===============================================================
@router.get("/me", response_model=dict)
def get_my_saved_jobs(
    candidate_id: str = Depends(candidate_session_required),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    query = (
        db.query(SavedJob, Job)
        .join(Job, SavedJob.job_id == Job.job_id)
        .filter(SavedJob.candidate_id == candidate_id)
        .order_by(SavedJob.saved_on.desc())
    )

    total = query.count()

    rows = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        SavedJobOut(
            job_id=job.job_id,
            title=job.title,
            location=job.location,
            work_mode=job.work_mode,
            status=job.status,
            saved_on=saved.saved_on,
        )
        for saved, job in rows
    ]

    logger.info(
        f"[SavedJob] Candidate={candidate_id} fetched page={page} size={len(items)}"
    )

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": (page * page_size) < total,
    }
