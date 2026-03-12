# app/schemas/screening_schemas.py
from datetime import datetime
from typing import Optional, List, Dict, Generic, TypeVar

from pydantic import BaseModel, Field, ConfigDict

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
# COMMON / PAGINATION (same pattern as other schema files)
# ============================================================
class MessageOut(BaseModel):
    message: str


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
# HELPER
# ============================================================
def split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


# ============================================================
# CORE SCREENING RESULT
# ============================================================
class ScreeningResultOut(ORMModel):
    application_id: str
    candidate_id: str
    job_id: str
    status: str
    score: Optional[float] = None
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ============================================================
# SINGLE / EXISTING SCREENING
# ============================================================
class ScreenOneResponse(MessageOut):
    application_id: str
    result: ScreeningResultOut


class ScreenOneExistingResponse(MessageOut):
    application_id: str
    existing_result: ScreeningResultOut


# ============================================================
# BATCH SCREENING
# ============================================================
class ScreenBatchRequest(BaseModel):
    application_ids: List[str]


class ScreenBatchItem(BaseModel):
    application_id: str
    status: str
    score: Optional[float] = None
    error: Optional[str] = None


class ScreenBatchResponse(MessageOut):
    total_applications: int
    successful: int
    failed: int
    results: List[ScreenBatchItem]


# ============================================================
# SCREENING STATUS (PER APPLICATION)
# ============================================================
class ScreeningStatusResponse(BaseModel):
    application_id: str
    status: str
    score: Optional[float] = None
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ============================================================
# JOB LEVEL SCREENING (STATS + LIST)
# ============================================================
class JobScreeningStats(BaseModel):
    total_applications: int
    shortlisted: int
    on_hold: int
    rejected: int
    shortlist_rate: float


class JobScreeningsResponse(BaseModel):
    job_id: str
    statistics: JobScreeningStats
    screenings: List[ScreeningResultOut]


class JobScreeningsPaginatedResponse(BaseModel):
    job_id: str
    statistics: JobScreeningStats
    results: PaginatedResponse[ScreeningResultOut]


# ============================================================
# ATS CONFIG / FEATURES
# ============================================================
class ATSFeaturesOut(BaseModel):
    real_time_processing: bool
    email_notifications: bool
    analytics: bool
    fuzzy_matching: bool


class ATSConfigResponse(BaseModel):
    ats_configuration: Dict
    features: ATSFeaturesOut


# ============================================================
# ANALYTICS
# ============================================================
class AnalyticsEmptyResponse(MessageOut):
    total_screenings: int
    statistics: Dict


class AnalyticsStats(BaseModel):
    shortlisted: int
    on_hold: int
    rejected: int
    shortlist_rate: float
    average_score: float


class AnalyticsSummaryResponse(BaseModel):
    total_screenings: int
    statistics: AnalyticsStats
    ats_version: str
