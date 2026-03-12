# app/schemas/feedback_schemas.py
from __future__ import annotations

from typing import List, Optional
from typing_extensions import Annotated
from datetime import datetime

from pydantic import (
    BaseModel,
    Field,
    EmailStr,
    ConfigDict,
    model_validator,
    StringConstraints,
)

from app.models.job_models import RoundDecision


# ============================================================
# COMMON CONSTRAINED TYPES (REUSABLE)
# ============================================================
NameStr120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
NameStr100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ShortStr50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Text255 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
LongText2000 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
PhoneStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=15)]


# ============================================================
# PANEL FEEDBACK (InterviewFeedback)
# ============================================================
class PanelFeedbackCreate(BaseModel):
    interview_id: int

    panel_member_name: NameStr120
    panel_member_email: EmailStr

    attended: bool = True
    questions_asked: Optional[list] = None
    feedback_text: Optional[LongText2000] = None
    feedback_rating: Optional[int] = Field(None, ge=1, le=10)
    decision: Optional[RoundDecision] = None
    reason: Optional[Text255] = None

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.decision == RoundDecision.reject and not self.reason:
            raise ValueError("reason is required when decision is 'reject'")
        return self


class PanelFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    feedback_id: int
    interview_id: int

    panel_member_name: str
    panel_member_email: EmailStr
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


class PanelFeedbackListOut(BaseModel):
    items: List[PanelFeedbackOut]
    total: int


# ============================================================
# CANDIDATE FEEDBACK (OPTIONAL / POST-INTERVIEW)
# ============================================================
class CandidateRoundItem(BaseModel):
    round_no: Optional[int] = None
    label: Optional[str] = None
    interview_type: Optional[str] = None
    notes: Optional[str] = None


class CandidateFeedbackCreate(BaseModel):
    interview_id: int

    name: NameStr100
    phone_number: PhoneStr
    year_of_passed: int

    education: NameStr100
    attended_company: NameStr100
    vendor_guided_you: NameStr100
    recruiter_name: NameStr100

    no_of_interview_rounds_from_client: int = Field(..., ge=0)
    level_of_interview: ShortStr50

    rounds: List[CandidateRoundItem] = Field(..., min_items=1)

    question_textbox: str
    experience_about_interview: str


class CandidateFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interview_id: int

    name: str
    phone_number: str
    year_of_passed: int
    education: str
    attended_company: str
    vendor_guided_you: str
    recruiter_name: str
    no_of_interview_rounds_from_client: int
    level_of_interview: str

    rounds: list
    question_textbox: str
    experience_about_interview: str


class CandidateFeedbackListOut(BaseModel):
    items: List[CandidateFeedbackOut]
    total: int
