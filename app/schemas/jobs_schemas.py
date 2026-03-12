# app/schemas/jobs_schemas.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Literal, Dict, Generic, TypeVar
from datetime import datetime, date

# ============================================================
# ORM compatibility (Pydantic v1 + v2)
# ============================================================
try:
    from pydantic import ConfigDict

    class ORMModel(BaseModel):
        model_config = ConfigDict(from_attributes=True)
except Exception:
    class ORMModel(BaseModel):
        class Config:
            from_attributes = True


# ============================================================
# PAGINATION (REUSED ACROSS APP)
# ============================================================
class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_records: int
    total_pages: int
    has_next: bool
    has_previous: bool


T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    data: List[T]
    pagination: PaginationMeta

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )



# ============================================================
# COMPANY (MINI)
# ============================================================
class CompanyMiniOut(ORMModel):
    company_id: str
    name: str
    logo_url: Optional[str] = None


# ============================================================
# JOB CREATE
# ============================================================
class JobCreate(BaseModel):
    company_id: str
    title: str
    job_type: str
    description: str
    work_mode: str

    responsibilities: str
    primary_skills: str
    secondary_skills: Optional[str] = None

    qualification_requirement: str

    total_experience: str
    relevant_experience: Optional[str] = None

    salary: Optional[str] = None
    location: str
    designation: str
    band: str

    number_of_positions: int = 1
    category: Optional[str] = None

    application_deadline: Optional[date] = None
    status: Optional[str] = "Open"


# ============================================================
# JOB UPDATE
# ============================================================
class JobUpdate(BaseModel):
    company_id: Optional[str] = None
    title: Optional[str] = None
    job_type: Optional[str] = None
    description: Optional[str] = None
    work_mode: Optional[str] = None

    responsibilities: Optional[str] = None
    primary_skills: Optional[str] = None
    secondary_skills: Optional[str] = None

    qualification_requirement: Optional[str] = None
    total_experience: Optional[str] = None
    relevant_experience: Optional[str] = None

    salary: Optional[str] = None
    location: Optional[str] = None
    designation: Optional[str] = None
    band: Optional[str] = None

    number_of_positions: Optional[int] = None
    category: Optional[str] = None

    application_deadline: Optional[date] = None
    status: Optional[str] = None


# ============================================================
# JOB OUTPUT
# ============================================================
class JobOut(ORMModel):
    job_id: str
    title: str
    job_type: str
    description: str
    work_mode: str

    responsibilities: str
    primary_skills: str
    secondary_skills: Optional[str] = None

    qualification_requirement: str
    total_experience: str
    relevant_experience: Optional[str] = None

    salary: Optional[str] = None
    location: str
    designation: str
    band: str

    number_of_positions: int
    category: Optional[str] = None

    application_deadline: Optional[date] = None
    status: str
    date_posted: datetime

    company: Optional[CompanyMiniOut] = None


# ============================================================
# JOB LIST (FOR SEARCH / DASHBOARD)
# ============================================================
class JobListItem(JobOut):
    pass


# ============================================================
# APPLICATIONS UNDER A JOB
# ============================================================
class JobApplicationItem(BaseModel):
    application_id: str
    candidate_id: str
    name: str
    email: str
    status: str
    screening_status: Optional[str] = None
    screening_score: Optional[float] = None


class JobApplicationList(BaseModel):
    job_id: str
    total: int
    items: List[JobApplicationItem]


class ApplicationStatusUpdate(BaseModel):
    status: Literal["shortlisted", "on_hold", "rejected", "hired"]
    reason: Optional[str] = None


# ============================================================
# JOB METRICS & DETAIL
# ============================================================
class JobMetricsOut(BaseModel):
    total_applicants: int
    by_status: Dict[str, int]
    last_applied_at: Optional[datetime] = None


class JobDetailOut(BaseModel):
    job: JobOut
    metrics: JobMetricsOut


# ============================================================
# SAVED JOBS
# ============================================================
class SavedJobCreate(BaseModel):
    job_id: str


class SavedJobOut(ORMModel):
    job_id: str
    title: str
    location: str
    work_mode: str
    status: str
    saved_on: datetime
