# app/schemas/interview_schemas.py
from __future__ import annotations

from typing import List, Optional, Literal
from datetime import date, time, datetime
import re

from pydantic import (
    BaseModel,
    Field,
    EmailStr,
    field_validator,
    model_validator,
    ConfigDict,
)
from pydantic_core.core_schema import ValidationInfo

from app.models.job_models import (
    ExperienceType,
    InterviewType,
    RoundStatus,
    RoundDecision,
)

# ============================================================
# CONSTANTS
# ============================================================
EXP_REGEX = re.compile(
    r"^[ ]*\d+(\.\d+)?([ ]*-[ ]*\d+(\.\d+)?)?[ ]*(years?|yrs?)?[ ]*$",
    re.IGNORECASE,
)


# ============================================================
# PANEL MEMBER
# ============================================================
class PanelMember(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr

    # Server-filled fields
    feedback_token: Optional[str] = None
    feed_back_link: Optional[str] = None
    submitted: Optional[bool] = False
    submitted_at: Optional[datetime] = None


# ============================================================
# CREATE INTERVIEW
# ============================================================
class InterviewCreate(BaseModel):
    candidate_id: str
    job_id: str

    experience_type: ExperienceType
    total_experience_years: Optional[str] = None
    relevant_experience_years: Optional[str] = None

    technology: str = Field(..., max_length=100)
    level_number: int = Field(..., ge=1)
    round_category: Literal["technical", "hr", "manageral"] = "technical"
    round_name: str = Field(..., max_length=80)

    date: date
    start_time: time
    end_time: time
    interview_timezone: str = "Asia/Kolkata"

    interview_type: InterviewType
    interview_link: Optional[str] = None
    location_full: Optional[str] = Field(None, max_length=255)

    panel_members: List[PanelMember] = Field(..., min_items=1, max_items=6)
    created_by: Optional[str] = Field(None, max_length=50)

    @field_validator("total_experience_years", "relevant_experience_years")
    @classmethod
    def validate_experience(cls, v: Optional[str], info: ValidationInfo):
        exp_type = info.data.get("experience_type")

        if exp_type == ExperienceType.fresher:
            if v not in (None, ""):
                raise ValueError("Fresher must not provide experience fields.")
            return None

        if not v:
            raise ValueError("Experience is required for experienced candidates.")

        if not EXP_REGEX.match(v):
            raise ValueError(
                "Experience must be like '3 years', '4.5 yrs', or '3-4 years'."
            )
        return v

    @model_validator(mode="after")
    def validate_time_and_mode(self):
        if not self.start_time < self.end_time:
            raise ValueError("start_time must be earlier than end_time.")

        if self.interview_type == InterviewType.virtual and not self.interview_link:
            raise ValueError("Virtual interview requires interview_link.")

        if self.interview_type == InterviewType.physical and not self.location_full:
            raise ValueError("In-person interview requires location_full.")

        return self


# ============================================================
# FEEDBACK (PANEL)
# ============================================================
class InterviewFeedbackCreate(BaseModel):
    submitted_by_name: str = Field(..., max_length=120)
    submitted_by_email: EmailStr
    attended: bool = True
    questions_asked: Optional[list] = None
    feedback_text: Optional[str] = Field(None, max_length=2000)
    feedback_rating: Optional[int] = Field(None, ge=1, le=10)
    decision: Optional[RoundDecision] = None
    reason: Optional[str] = Field(None, max_length=255)

    @model_validator(mode="after")
    def validate_feedback(self):
        if self.decision == RoundDecision.reject and not self.reason:
            raise ValueError("reason is required when decision is 'reject'.")
        return self


class ApproveFeedback(BaseModel):
    approve: Literal[True]
    approved_by_email: EmailStr


class RejectFeedback(BaseModel):
    approve: Literal[False]
    approved_by_email: EmailStr
    reason: Optional[str] = Field(None, max_length=255)


# ============================================================
# FINAL RESULT UPDATE
# ============================================================
class InterviewUpdateResult(BaseModel):
    status: Literal["passed", "failed"]
    attended: bool = True
    feedback_text: Optional[str] = Field(None, max_length=2000)
    feedback_rating: Optional[int] = Field(None, ge=1, le=10)
    decision: Optional[RoundDecision] = None
    reason: Optional[str] = Field(None, max_length=255)
    evaluated_by_name: Optional[str] = Field(None, max_length=120)
    evaluated_by_email: Optional[EmailStr] = None

    @model_validator(mode="after")
    def validate_result(self):
        if self.attended and not self.evaluated_by_email:
            raise ValueError("evaluated_by_email is required when attended is true.")
        if self.decision == RoundDecision.reject and not self.reason:
            raise ValueError("reason is required when decision is 'reject'.")
        return self


# ============================================================
# RESCHEDULE / GENERIC
# ============================================================
class InterviewReschedule(BaseModel):
    date: date
    start_time: time
    end_time: time
    interview_timezone: Optional[str] = None
    reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_time(self):
        if not self.start_time < self.end_time:
            raise ValueError("start_time must be earlier than end_time.")
        return self


class ReasonOnly(BaseModel):
    reason: str = Field(..., min_length=2, max_length=255)


class PanelUpdate(BaseModel):
    panel_members: List[PanelMember] = Field(..., min_items=1, max_items=6)


# ============================================================
# OUTPUT MODELS
# ============================================================
class InterviewFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    feedback_id: int
    interview_id: int
    submitted_by_name: str
    submitted_by_email: EmailStr
    submitted_at: datetime
    attended: bool
    questions_asked: Optional[list]
    feedback_text: Optional[str]
    feedback_rating: Optional[int]
    decision: Optional[RoundDecision]
    reason: Optional[str]
    approved: bool
    approved_by_email: Optional[EmailStr]
    approved_at: Optional[datetime]


class InterviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    interview_id: int
    candidate_id: str
    job_id: str

    candidate_first_name: str
    candidate_last_name: str
    candidate_email: EmailStr

    experience_type: ExperienceType
    total_experience_years: Optional[str]
    relevant_experience_years: Optional[str]

    technology: str
    level_number: int
    round_category: str
    round_name: str

    date: date
    start_time: time
    end_time: time
    interview_timezone: str

    interview_type: InterviewType
    interview_link: Optional[str]
    location_full: Optional[str]

    panel_members: List[PanelMember]

    actual_start_at: Optional[datetime]
    actual_end_at: Optional[datetime]

    attended: bool
    feedback_text: Optional[str]
    feedback_rating: Optional[int]
    decision: Optional[RoundDecision]
    evaluated_by_name: Optional[str]
    evaluated_by_email: Optional[EmailStr]
    evaluated_at: Optional[datetime]

    status: RoundStatus
    reason: Optional[str]

    created_by: str
    created_at: datetime
    updated_at: datetime


# ============================================================
# PAGINATED GRIDS (ADMIN / UI)
# ============================================================
class InterviewPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    items: List[InterviewOut]
    total: int
    limit: int
    offset: int


class MetaOut(BaseModel):
    interview_types: List[str]
    round_statuses: List[str]
    round_decisions: List[str]
    round_label_hint: str


class AdminFeedbackRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feedback_id: int
    interview_id: int
    submitted_at: datetime
    candidate_name: str
    candidate_email: EmailStr
    job_title: str
    job_code: Optional[str] = None
    scheduled_date: Optional[date] = None
    interviewer_name: str
    rating: Optional[int] = None
    recommendation: str
    status: Literal["pending review", "approved", "rejected"]


class AdminFeedbackPageOut(BaseModel):
    items: List[AdminFeedbackRowOut]
    total: int
    limit: int
    offset: int


class AdminInterviewRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    interview_id: int
    candidate_name: str
    candidate_email: EmailStr
    job_title: str
    job_code: Optional[str] = None
    interview_date: date
    interviewer_name: Optional[str] = None
    level_label: str
    status_chip: Literal[
        "scheduled",
        "completed",
        "cancelled",
        "rescheduled",
        "no_show",
        "passed",
        "failed",
    ]


class AdminInterviewPageOut(BaseModel):
    items: List[AdminInterviewRowOut]
    total: int
    limit: int
    offset: int


class AdminApplicationRowOut(BaseModel):
    application_id: str
    candidate_id: str
    candidate_name: str
    candidate_email: EmailStr
    job_id: str
    job_title: str
    job_location: str
    applied_date: datetime
    status: str
    source: str
    is_referral: bool


class AdminApplicationsPageOut(BaseModel):
    items: List[AdminApplicationRowOut]
    total: int
    limit: int
    offset: int


class AdminApplicationDetailOut(BaseModel):
    application_id: str
    status: str
    source: str
    is_referral: bool
    applied_at: datetime
    candidate_id: str
    candidate_name: str
    candidate_email: EmailStr
    contact_number: Optional[str] = None
    job_id: str
    job_title: str
    job_location: str
    work_mode: str
    total_experience: str
