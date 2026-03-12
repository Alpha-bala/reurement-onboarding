from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_
from datetime import date

from app.database import get_db
from app.models.job_models import Job, Company
from app.models.user_models import CandidateProfileInformation

router = APIRouter(
    prefix="/public",
    tags=["Public Homepage APIs"],
)

# ================================================================
#  HOMEPAGE STATS  →  /public/home-stats
# ================================================================
@router.get("/home-stats")
def get_home_stats(db: Session = Depends(get_db)):
    total_jobs = db.query(Job).filter(Job.status == "Open").count()
    total_companies = db.query(Company).count()
    total_candidates = db.query(CandidateProfileInformation).count()

    new_jobs_today = db.query(Job).filter(
        func.date(Job.date_posted) == date.today(),
        Job.status == "Open",
    ).count()

    return {
        "total_jobs": total_jobs,
        "total_companies": total_companies,
        "total_candidates": total_candidates,
        "new_jobs_today": new_jobs_today,
    }


# ================================================================
# POPULAR CATEGORIES  →  /public/popular-categories
# ================================================================
@router.get("/popular-categories")
def get_popular_categories(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Job.category,
            func.count(Job.job_id).label("open_positions"),
        )
        .filter(
            Job.status == "Open",
            Job.category.isnot(None),
        )
        .group_by(Job.category)
        .order_by(desc("open_positions"))
        .limit(12)
        .all()
    )

    return [
        {"category": category, "open_positions": count}
        for category, count in rows
    ]


# ================================================================
# FEATURED JOBS  →  /public/featured-jobs
# ================================================================
@router.get("/featured-jobs")
def get_featured_jobs(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Job,
            func.count(Job.applications).label("app_count"),
        )
        .join(Job.applications)
        .filter(Job.status == "Open")
        .group_by(Job.job_id)
        .order_by(desc("app_count"))
        .limit(10)
        .all()
    )

    return [
        {
            "job_id": job.job_id,
            "title": job.title,
            "job_type": job.job_type,
            "salary": job.salary,
            "company_name": job.company.name if job.company else None,
            "company_logo": job.company.logo_url if job.company else None,
            "location": job.location,
            "applications": count,
        }
        for job, count in rows
    ]


# ================================================================
# LATEST JOBS  →  /public/latest-jobs
# ================================================================
@router.get("/latest-jobs")
def get_latest_jobs(db: Session = Depends(get_db)):
    jobs = (
        db.query(Job)
        .filter(Job.status == "Open")
        .order_by(desc(Job.date_posted))
        .limit(15)
        .all()
    )

    return [
        {
            "job_id": job.job_id,
            "title": job.title,
            "salary": job.salary,
            "location": job.location,
            "company_name": job.company.name if job.company else None,
            "company_logo": job.company.logo_url if job.company else None,
            "posted_on": job.date_posted,
        }
        for job in jobs
    ]


# ================================================================
# TOP COMPANIES  →  /public/top-companies
# ================================================================
@router.get("/top-companies")
def get_top_companies(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Company,
            func.count(Job.job_id).label("open_positions"),
        )
        .outerjoin(Job, Company.company_id == Job.company_id)
        .filter(Job.status == "Open")
        .group_by(Company.company_id)
        .order_by(desc("open_positions"))
        .limit(12)
        .all()
    )

    return [
        {
            "company_id": company.company_id,
            "name": company.name,
            "logo_url": company.logo_url,
            "location": company.location,
            "open_positions": count,
        }
        for company, count in rows
    ]


# ================================================================
# SEARCH JOBS  →  /public/search  (PAGINATED)
# ================================================================
@router.get("/search")
def search_jobs(
    q: str = Query(..., min_length=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    search = f"%{q.strip().lower()}%"

    base_query = db.query(Job).filter(
        Job.status == "Open",
        or_(
            func.lower(Job.title).like(search),
            func.lower(Job.description).like(search),
            func.lower(Job.location).like(search),
            func.lower(Job.primary_skills).like(search),
            func.lower(Job.secondary_skills).like(search),
        ),
    )

    total = base_query.count()

    jobs = (
        base_query
        .order_by(desc(Job.date_posted))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        {
            "job_id": job.job_id,
            "title": job.title,
            "location": job.location,
            "salary": job.salary,
            "company_name": job.company.name if job.company else None,
            "company_logo": job.company.logo_url if job.company else None,
            "posted_on": job.date_posted,
        }
        for job in jobs
    ]

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": (page * page_size) < total,
    }
