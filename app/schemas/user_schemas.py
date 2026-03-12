# app/schemas/user_schemas.py
from pydantic import BaseModel, EmailStr, validator, Field,ConfigDict
from typing import Optional, List, Dict, Literal, Any, Generic, TypeVar
from datetime import datetime, date
from enum import Enum

# ============================================================
# ORM COMPATIBILITY (Pydantic v1 + v2)
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
# COMMON BASE RESPONSES (FRONTEND FRIENDLY)
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
# OTP
# ============================================================
class OTPPurpose(str, Enum):
    signup = "signup"
    reset = "reset"
    forgot = "forgot"


class OTPSendIn(BaseModel):
    email: EmailStr
    purpose: OTPPurpose


class OTPSendOut(BaseModel):
    otp_request_id: str
    expires_at: datetime
    resend_in_seconds: int


class OTPVerifyIn(BaseModel):
    otp_request_id: str
    otp: str


class OTPVerifyOut(BaseModel):
    verified: bool
    email: EmailStr
    purpose: OTPPurpose
    verified_at: datetime


# ============================================================
# AUTH / LOGIN
# ============================================================
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    mfa_enabled: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ResetPasswordIn(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str
    otp_request_id: Optional[str] = None


# ============================================================
# SKILLS
# ============================================================
class SkillItems(BaseModel):
    primary_skills: Optional[List[str]] = None
    secondary_skills: Optional[List[str]] = None


# ============================================================
# REGISTRATION
# ============================================================
class CandidateRegister(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    contact_number: str
    highest_qualification: Optional[str] = None
    total_experience: Optional[str] = None
    relevant_experience: Optional[str] = None
    skills: Optional[SkillItems] = None
    password: str
    is_active: bool = True
    otp_request_id: Optional[str] = None


class CandidateRegisterOut(MessageOut):
    candidate_id: str


class CandidateLogin(BaseModel):
    email: EmailStr
    password: str


# ============================================================
# PROFILE – EDUCATION & EXPERIENCE
# ============================================================
class EducationItems(BaseModel):
    degree: str
    institute: str
    passed_year: int
    specialization: Optional[str] = None
    percentage: Optional[float] = None
    education_gap: Optional[bool] = None
    education_gap_reason: Optional[str] = None

    @validator("passed_year")
    def validate_year(cls, v):
        if v < 1900 or v > date.today().year:
            raise ValueError(f"Passed out year must be between 1900 and {date.today().year}")
        return v


class ExperienceItem(BaseModel):
    company_name: str
    designation: str
    start_date: str
    end_date: Optional[str] = None
    description: Optional[str] = None
    reason_for_separation: Optional[str] = None
    notice_period: Optional[int] = None
    current_ctc: Optional[float] = None
    is_current: bool = False

    @validator("start_date", "end_date", pre=True)
    def validate_dates(cls, v):
        if v:
            date.fromisoformat(v)
        return v


class CandidateProfileUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    contact_number: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[str] = None

    highest_qualification: Optional[str] = None
    education: Optional[Dict[int, EducationItems]] = None
    skills: Optional[SkillItems] = None
    total_experience: Optional[str] = None
    relevant_experience: Optional[str] = None
    experience: Optional[Dict[int, ExperienceItem]] = None


# ============================================================
# APPLICATIONS
# ============================================================
class MyApplicationJobOut(BaseModel):
    job_id: str
    title: str
    location: Optional[str] = None
    work_mode: Optional[str] = None
    experience: Optional[str] = None
    skills: Optional[str] = None


class MyApplicationOut(BaseModel):
    application_id: str
    job: MyApplicationJobOut
    status: str
    applied_at: datetime
    source: str


# ============================================================
# DOCUMENTS
# ============================================================
class CandidateDocumentOut(ORMModel):
    doc_id: int
    doc_name: str
    doc_sub_name: Optional[str] = None
    file_name: str
    file_path: str
    uploaded_at: datetime


# ============================================================
# FULL PROFILE RESPONSE
# ============================================================
class CandidateFullProfileOut(BaseModel):
    candidate_id: str
    first_name: str
    last_name: str
    email: EmailStr
    contact_number: str
    date_of_birth: Optional[date]
    gender: Optional[str]
    address: Optional[str]
    highest_qualification: Optional[str]
    education: Optional[Dict[int, EducationItems]]
    skills: Optional[SkillItems]
    experience: Optional[Dict[int, ExperienceItem]]
    total_experience: Optional[str]
    relevant_experience: Optional[str]
    documents: List[CandidateDocumentOut]


# ============================================================
# APPLY JOB RESPONSE
# ============================================================
class ApplyJobOut(MessageOut):
    applied_via: Literal["referral", "external"]
    referral_id: Optional[str] = None
    application_id: str
    job_id: str
    candidate_id: str
    first_name: str
    last_name: str
    email: EmailStr
    contact_number: str
    status: str
    source: str
    employee_id: Optional[str] = None
    email_sent: bool
    email_error: Optional[str] = None
    screening_queued: bool


# ============================================================
# NOTIFICATIONS
# ============================================================
class NotificationMeta(BaseModel):
    routh_path:Optional[str] = None
    http_method:Optional[str] = None


class NotificationStructure(BaseModel):
    scenario:Optional[str] = None
    meta_data:Optional[NotificationMeta] = None
    message:Optional[str] = None
    timestamp:str = str(datetime.now())


# ============================================================
# MFA
# ============================================================
class CandidateLoginMFAOut(BaseModel):
    mfa_required: bool = True
    enroll_required: bool
    user_id: str
    role: str = "CANDIDATE"
    secret_key: Optional[str] = None
    message: str


class MFAVerifyIn(BaseModel):
    code: str


class MFAVerifyOut(BaseModel):
    verified: bool
    message: str
    user_id: str
    role: str = "CANDIDATE"
