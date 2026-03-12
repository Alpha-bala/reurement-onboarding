from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
import logging

from app.database import get_db
from app.models.job_models import Application
from app.models.screening_models import ResumeScreening
from app.schemas.screening_schemas import (
    ScreeningResultOut,
    ScreeningStatusResponse,
    ScreenBatchResponse,
    ScreenOneResponse,
    ScreenOneExistingResponse,
    ScreenBatchItem,
    ScreenBatchRequest,
    JobScreeningsResponse,
    JobScreeningStats,
    split_csv,
)
from app.services.screening_services import screen_resume, screen_multiple_applications
from app.utils.helper import require_hr_or_superadmin
from app.utils.guards import hr_session_required

logger = logging.getLogger("app.routers.screening")
logger.setLevel(logging.INFO)

router = APIRouter(
    prefix="/screening",
    tags=["Resume Screening"],
    dependencies=[
        Depends(require_hr_or_superadmin),
        Depends(hr_session_required),
    ],
)

# ==============================================================================
# RUN SCREENING FOR ONE APPLICATION
# ==============================================================================
@router.post("/{application_id}", response_model=ScreenOneResponse | ScreenOneExistingResponse)
def run_screening(application_id: str, db: Session = Depends(get_db)):
    try:
        application = db.query(Application).filter(
            Application.application_id == application_id
        ).first()

        if not application:
            raise HTTPException(status_code=404, detail="Application not found")

        existing = db.query(ResumeScreening).filter(
            ResumeScreening.application_id == application_id
        ).first()

        if existing:
            return {
                "message": "Screening already completed",
                "application_id": application_id,
                "existing_result": {
                    "application_id": existing.application_id,
                    "candidate_id": existing.candidate_id,
                    "job_id": existing.job_id,
                    "status": existing.status,
                    "score": existing.score,
                    "matched_skills": split_csv(existing.matched_skills),
                    "missing_skills": split_csv(existing.missing_skills),
                    "created_at": existing.created_at,
                    "updated_at": existing.updated_at,
                },
            }

        result = screen_resume(db, application_id)
        if isinstance(result, dict) and result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error", "Screening failed"))

        saved = db.query(ResumeScreening).filter(
            ResumeScreening.application_id == application_id
        ).first()

        if not saved:
            raise HTTPException(status_code=500, detail="Screening failed to persist result")

        return {
            "message": "Screening completed successfully",
            "application_id": application_id,
            "result": {
                "application_id": saved.application_id,
                "candidate_id": saved.candidate_id,
                "job_id": saved.job_id,
                "status": saved.status,
                "score": saved.score,
                "matched_skills": split_csv(saved.matched_skills),
                "missing_skills": split_csv(saved.missing_skills),
                "created_at": saved.created_at,
                "updated_at": saved.updated_at,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Screening] Failed for {application_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Screening failed")


# ==============================================================================
# BATCH SCREENING
# ==============================================================================
@router.post("/batch", response_model=ScreenBatchResponse)
def run_batch_screening(payload: ScreenBatchRequest, db: Session = Depends(get_db)):
    application_ids = payload.application_ids

    if not application_ids:
        raise HTTPException(status_code=400, detail="No application IDs provided")
    if len(application_ids) > 50:
        raise HTTPException(status_code=400, detail="Batch size cannot exceed 50 applications")

    try:
        results_raw = screen_multiple_applications(db, application_ids)

        screenings = {
            s.application_id: s
            for s in db.query(ResumeScreening)
            .filter(ResumeScreening.application_id.in_(application_ids))
            .all()
        }

        items: List[ScreenBatchItem] = []
        success_count = 0

        for app_id in application_ids:
            saved = screenings.get(app_id)
            if saved:
                items.append(
                    ScreenBatchItem(
                        application_id=app_id,
                        status=saved.status or "unknown",
                        score=saved.score,
                    )
                )
                success_count += 1
            else:
                err_msg = next(
                    (r.get("error") for r in results_raw if r.get("application_id") == app_id),
                    "Unknown error",
                )
                items.append(
                    ScreenBatchItem(
                        application_id=app_id,
                        status="error",
                        error=err_msg,
                    )
                )

        error_count = len(application_ids) - success_count

        return ScreenBatchResponse(
            message=f"Batch screening completed: {success_count} successful, {error_count} failed",
            total_applications=len(application_ids),
            successful=success_count,
            failed=error_count,
            results=items,
        )

    except Exception as e:
        logger.error(f"[Batch Screening] Failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Batch screening failed")


# ==============================================================================
# GET SCREENING STATUS
# ==============================================================================
@router.get("/status/{application_id}", response_model=ScreeningStatusResponse)
def get_screening_status(application_id: str, db: Session = Depends(get_db)):
    screening = db.query(ResumeScreening).filter(
        ResumeScreening.application_id == application_id
    ).first()

    if not screening:
        raise HTTPException(status_code=404, detail="Screening not found for this application")

    return ScreeningStatusResponse(
        application_id=application_id,
        status=screening.status,
        score=screening.score,
        matched_skills=split_csv(screening.matched_skills),
        missing_skills=split_csv(screening.missing_skills),
        created_at=screening.created_at,
        updated_at=screening.updated_at,
    )


# ==============================================================================
# GET JOB SCREENINGS (PAGINATED)
# ==============================================================================
@router.get("/job/{job_id}", response_model=JobScreeningsResponse)
def get_job_screenings(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    query = db.query(ResumeScreening).filter(
        ResumeScreening.job_id == job_id
    )

    total = query.count()

    screenings = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    screening_results: List[ScreeningResultOut] = [
        ScreeningResultOut(
            application_id=s.application_id,
            candidate_id=s.candidate_id,
            job_id=s.job_id,
            status=s.status,
            score=s.score,
            matched_skills=split_csv(s.matched_skills),
            missing_skills=split_csv(s.missing_skills),
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in screenings
    ]

    shortlisted = sum(1 for s in screening_results if s.status == "Shortlisted")
    on_hold = sum(1 for s in screening_results if s.status == "On Hold")
    rejected = sum(1 for s in screening_results if s.status == "Rejected")

    stats = JobScreeningStats(
        total_applications=total,
        shortlisted=shortlisted,
        on_hold=on_hold,
        rejected=rejected,
        shortlist_rate=round((shortlisted / total) * 100, 2) if total else 0.0,
    )

    return {
        "job_id": job_id,
        "statistics": stats,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": (page * page_size) < total,
        "screenings": screening_results,
    }
