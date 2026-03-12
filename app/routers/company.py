import mimetypes
from typing import List

from fastapi import APIRouter, Depends, UploadFile, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.job_models import Company
from app.schemas.company_schemas import (
    CompanyCreate,
    CompanyUpdate,
    CompanyOut,
)
from app.utils.s3_utils_1 import upload_to_s3
from app.utils.guards import hr_session_required


# ===============================================================
# Router Setup — HR only
# ===============================================================
router = APIRouter(
    prefix="/admin/companies",
    tags=["Admin Companies"],
    dependencies=[Depends(hr_session_required)],
)


# ===============================================================
# S3 UPLOAD HELPER (NO EXPIRY, PUBLIC URL)
# ===============================================================
def upload_company_logo_to_s3(logo: UploadFile, company_id: str) -> str:
    """
    Upload company logo to S3 (public, no expiry).
    Path:
      companies/{company_id}/logo/{filename}
    """
    if not logo or not logo.filename:
        raise HTTPException(status_code=400, detail="Invalid logo file")

    folder = f"companies/{company_id}/logo"
    return upload_to_s3(logo, folder=folder)


# ===============================================================
# CREATE COMPANY (JSON ONLY)
# ===============================================================
@router.post("/create", response_model=CompanyOut)
def create_company(
    request: CompanyCreate,
    db: Session = Depends(get_db),
):
    exists = db.query(Company.company_id).filter(
        func.lower(Company.name) == request.name.lower()
    ).first()

    if exists:
        raise HTTPException(status_code=400, detail="Company name already exists")

    company = Company(
        company_id=request.company_id,
        name=request.name,
        location=request.location,
        website=request.website,
        industry=request.industry,
        about=request.about,
    )

    db.add(company)
    db.commit()
    db.refresh(company)
    return company


# ===============================================================
# UPDATE COMPANY (JSON ONLY)
# ===============================================================
@router.put("/update/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: str,
    update: CompanyUpdate,
    db: Session = Depends(get_db),
):
    company = db.query(Company).filter_by(company_id=company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    updates = update.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(company, field, value)

    db.commit()
    db.refresh(company)
    return company


# ===============================================================
# UPLOAD / REPLACE COMPANY LOGO (NO EXPIRY)
# ===============================================================
@router.put("/{company_id}/upload-logo", response_model=CompanyOut)
def upload_company_logo(
    company_id: str,
    logo: UploadFile,
    db: Session = Depends(get_db),
):
    company = db.query(Company).filter_by(company_id=company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.logo_url = upload_company_logo_to_s3(logo, company_id)

    db.commit()
    db.refresh(company)
    return company


# ===============================================================
# DELETE COMPANY
# ===============================================================
@router.delete("/delete/{company_id}", response_model=dict)
def delete_company(
    company_id: str,
    db: Session = Depends(get_db),
):
    company = db.query(Company).filter_by(company_id=company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    db.delete(company)
    db.commit()
    return {"message": "Company deleted successfully"}


# ===============================================================
# GET ALL COMPANIES (NON-PAGINATED)
# ===============================================================
@router.get("/all", response_model=List[CompanyOut])
def get_all_companies(db: Session = Depends(get_db)):
    return db.query(Company).order_by(Company.name.asc()).all()


# ===============================================================
# GET ALL COMPANIES — PAGINATED
# ===============================================================
@router.get("/page", response_model=dict)
def get_companies_paginated(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    q = db.query(Company)

    total = q.count()
    items = (
        q.order_by(Company.name.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ===============================================================
# GET SINGLE COMPANY
# ===============================================================
@router.get("/{company_id}", response_model=CompanyOut)
def get_company(
    company_id: str,
    db: Session = Depends(get_db),
):
    company = db.query(Company).filter_by(company_id=company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    return company
