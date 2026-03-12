# app/schemas/offer_bgv_schemas.py
from __future__ import annotations

from typing import Optional, Union, List, Generic, TypeVar
from datetime import datetime, date, time

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# COMMON / PAGINATION (USED LATER, SAFE TO DEFINE NOW)
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
# PDF TEMPLATE SCHEMAS
# ============================================================
class MgrPdfTemplateBase(BaseModel):
    letter_type: str = Field(
        ...,
        description="Template type: offer_letter / experience / relieving / appraisal / fnf / terms"
    )
    file_path: str = Field(
        ...,
        description="Stored file path of the uploaded PDF template"
    )


class MgrPdfTemplateCreate(MgrPdfTemplateBase):
    """Payload for creating a PDF template."""
    pass


class MgrPdfTemplateOut(MgrPdfTemplateBase):
    """Response when viewing/listing templates."""
    id: int

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# OFFER LETTER – BASE (FLEXIBLE INPUT)
# ============================================================
class MgrOfferLetterBase(BaseModel):
    """
    Flexible, frontend-friendly schema.
    Parsing & normalization happens in service layer.
    """
    candidate_id: str = Field(..., description="Public candidate id like CAND0001")
    template_id: int = Field(..., description="MgrPdfTemplate.id to use")

    # Dates as ISO strings
    offer_date: str
    joining_date: str
    acceptance_deadline: Optional[str] = None

    # Times / reporting
    offer_time: Optional[str] = None
    reporting_time: Optional[str] = None
    reporting_person: Optional[str] = None
    reporting_contact_number: Optional[str] = None
    reporting_office_address: Optional[str] = None

    # Role / org
    position: str
    band: Optional[str] = None
    regime: Optional[str] = "Hybrid Work Mode"

    # Compensation (flexible)
    annual_ctc: Optional[Union[int, float, str]] = 0
    variable_pay: Optional[Union[int, float, str]] = 0

    # Admin / meta
    co_founder_and_director: Optional[str] = None
    office_timings: Optional[str] = None

    # Periods
    notice_period: Optional[Union[int, str]] = None
    probation_period: Optional[Union[int, str]] = None

    # Office info
    office_name: Optional[str] = None
    office_location: Optional[str] = None

    # UI / PDF flags
    show_salary_breakup: bool = True

    model_config = ConfigDict(from_attributes=True)


class MgrOfferLetterCreate(MgrOfferLetterBase):
    """Create payload."""
    pass


# ============================================================
# OFFER LETTER – OUTPUT
# ============================================================
class MgrOfferLetterOut(BaseModel):
    id: int
    candidate_id: str
    template_id: Optional[int] = None

    reference_number: Optional[str] = None

    offer_date: Optional[date] = None
    offer_time: Optional[time] = None
    joining_date: Optional[date] = None
    acceptance_deadline: Optional[date] = None

    reporting_time: Optional[str] = None
    reporting_person: Optional[str] = None
    reporting_contact_number: Optional[str] = None
    reporting_office_address: Optional[str] = None

    position: Optional[str] = None
    band: Optional[str] = None
    regime: Optional[str] = None

    # Stored as strings in ORM
    annual_ctc: Optional[str] = None
    variable_pay: Optional[str] = None

    co_founder_and_director: Optional[str] = None
    office_timings: Optional[str] = None

    notice_period: Optional[str] = None
    probation_period: Optional[str] = None

    token: str
    qr_path: str

    office_name: Optional[str] = None
    office_location: Optional[str] = None

    pdf_path: Optional[str] = None

    # Workflow
    candidate_status: Optional[str] = None
    viewed_at: Optional[datetime] = None
    downloaded_at: Optional[datetime] = None
    signed_uploaded_at: Optional[datetime] = None
    admin_reviewed_at: Optional[datetime] = None
    admin_review_remark: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# LIST RESPONSES (FOR DASHBOARDS)
# ============================================================
class OfferListItem(MgrOfferLetterOut):
    pass


class OfferListResponse(BaseModel):
    offers: List[OfferListItem]
