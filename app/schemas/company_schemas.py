# app/schemas/company_schemas.py
from __future__ import annotations

from typing import Optional, List
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict
from app.schemas.jobs_schemas import JobOut


# ============================================================
# BASE
# ============================================================
class CompanyBase(BaseModel):
    name: str
    location: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    about: Optional[str] = None


# ============================================================
# CREATE / UPDATE
# ============================================================
class CompanyCreate(CompanyBase):
    company_id: str


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    about: Optional[str] = None


# ============================================================
# OUTPUT
# ============================================================
class CompanyOut(CompanyBase):
    company_id: str
    logo_url: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# COMPANY + JOBS (DETAIL VIEW)
# ============================================================
class CompanyWithJobsOut(CompanyOut):
    jobs: List[JobOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# LIST VIEW (FOR DASHBOARDS – PAGINATION READY)
# ============================================================
class CompanyListOut(BaseModel):
    total: int
    items: List[CompanyOut]
