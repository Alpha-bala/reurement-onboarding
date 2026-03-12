# app/routers/interviews.py

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date

from app.database import get_db
from app.models.job_models import Interview, RoundStatus
from app.schemas.interview_schemas import InterviewOut, InterviewPageOut
from app.utils.guards import candidate_session_required


# ===============================================================
# Router Setup — Candidate only + Active session validation
# ===============================================================
router = APIRouter(
    prefix="/interviews",
    tags=["Interviews"],
    dependencies=[Depends(candidate_session_required)],
)


# ===============================================================
# 🔹 Internal helper: build filtered query (REUSED everywhere)
# ===============================================================
def _build_interview_query(
    db: Session,
    candidate_id: Optional[str] = None,
    job_id: Optional[str] = None,
    status: Optional[RoundStatus] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    q = db.query(Interview)

    if candidate_id:
        q = q.filter(Interview.candidate_id == candidate_id)
    if job_id:
        q = q.filter(Interview.job_id == job_id)
    if status:
        q = q.filter(Interview.status == status)
    if date_from:
        q = q.filter(Interview.date >= date_from)
    if date_to:
        q = q.filter(Interview.date <= date_to)

    return q


# ===============================================================
# Candidate: Own Interview History
# ===============================================================
@router.get(
    "/candidate",
    response_model=List[InterviewOut],
    response_model_exclude_none=True,
)
def get_candidate_interviews(
    candidate_id: str = Depends(candidate_session_required),
    db: Session = Depends(get_db),
):
    rows = (
        _build_interview_query(db, candidate_id=candidate_id)
        .order_by(
            Interview.level_number.asc(),
            Interview.date.asc(),
            Interview.start_time.asc(),
        )
        .all()
    )
    return rows


# ===============================================================
# Get Interview by ID
# ===============================================================
@router.get(
    "/{interview_id}",
    response_model=InterviewOut,
    response_model_exclude_none=True,
)
def get_interview(interview_id: int, db: Session = Depends(get_db)):
    interview = (
        db.query(Interview)
        .filter(Interview.interview_id == interview_id)
        .first()
    )

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    return interview


# ===============================================================
# Generic List (Read-Only, Candidate Access)
# ===============================================================
@router.get(
    "",
    response_model=List[InterviewOut],
    response_model_exclude_none=True,
)
def list_interviews(
    db: Session = Depends(get_db),
    candidate_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    status: Optional[RoundStatus] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    rows = (
        _build_interview_query(
            db=db,
            candidate_id=candidate_id,
            job_id=job_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
        .order_by(
            Interview.date.asc(),
            Interview.start_time.asc(),
        )
        .all()
    )

    return rows


# ===============================================================
# Paginated Interview List (Read-Only)
# ===============================================================
@router.get(
    "/page",
    response_model=InterviewPageOut,
    response_model_exclude_none=True,
)
def list_interviews_page(
    db: Session = Depends(get_db),
    candidate_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    status: Optional[RoundStatus] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = _build_interview_query(
        db=db,
        candidate_id=candidate_id,
        job_id=job_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )

    total = q.count()

    items = (
        q.order_by(
            Interview.date.asc(),
            Interview.start_time.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return InterviewPageOut(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ===============================================================
# List Interviews by Job ID (Read-Only)
# ===============================================================
@router.get(
    "/job/{job_id}",
    response_model=List[InterviewOut],
    response_model_exclude_none=True,
)
def list_interviews_by_job(
    job_id: str,
    db: Session = Depends(get_db),
):
    rows = (
        _build_interview_query(db=db, job_id=job_id)
        .order_by(
            Interview.date.asc(),
            Interview.start_time.asc(),
        )
        .all()
    )

    return rows
