# app/schemas/bgv.py
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field
from app.models.bgv import BGVStatus, EmergencyRelation


# ============================================================
# EDUCATION
# ============================================================
class BGVEducationIn(BaseModel):
    qualification: Optional[str] = None
    institution_name: Optional[str] = None
    university_or_board: Optional[str] = None
    year_of_passing: Optional[int] = None
    percentage_or_cgpa: Optional[str] = None


class BGVEducationOut(BGVEducationIn):
    id: int

    class Config:
        from_attributes = True


# ============================================================
# EMPLOYMENT
# ============================================================
class BGVEmploymentVerifierIn(BaseModel):
    name: str
    designation: str
    contact_number: str


class BGVEmploymentIn(BaseModel):
    company_name: Optional[str] = None
    designation: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    reason_for_leaving: Optional[str] = None
    verifiers: Optional[List[BGVEmploymentVerifierIn]] = None


class BGVEmploymentVerifierOut(BGVEmploymentVerifierIn):
    pass


class BGVEmploymentOut(BaseModel):
    id: int
    company_name: Optional[str] = None
    designation: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    reason_for_leaving: Optional[str] = None
    verifiers: Optional[List[BGVEmploymentVerifierOut]] = None

    class Config:
        from_attributes = True

    @staticmethod
    def _format_date(value):
        if isinstance(value, date):
            return value.strftime("%m/%Y")
        return value

    def model_post_init(self, __context):
        object.__setattr__(self, "start_date", self._format_date(self.start_date))
        object.__setattr__(self, "end_date", self._format_date(self.end_date))


# ============================================================
# REFERENCES
# ============================================================
class BGVReferenceIn(BaseModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    company: Optional[str] = None
    relationship: Optional[str] = None
    contact_number: Optional[str] = None
    email: Optional[EmailStr] = None


class BGVReferenceOut(BGVReferenceIn):
    id: int

    class Config:
        from_attributes = True


# ============================================================
# PERSONAL DETAILS
# ============================================================
class BGVPersonalIn(BaseModel):
    candidate_id: str

    full_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    spouse_name: Optional[str] = None

    dob: Optional[date] = None
    gender: Optional[str] = None
    nationality: Optional[str] = None
    marital_status: Optional[str] = None

    aadhaar_number: Optional[str] = None
    pan_number: Optional[str] = None
    uan_number: Optional[str] = None
    provident_fund_number: Optional[str] = None
    passport_number: Optional[str] = None

    permanent_address: Optional[str] = None
    current_address: Optional[str] = None

    contact_number: Optional[str] = None
    alternate_number: Optional[str] = None
    email: Optional[EmailStr] = None

    emergency_contact_number: Optional[str] = None
    emergency_contact_relationship: Optional[EmergencyRelation] = None
    emergency_contact_name: Optional[str] = None


class BGVPersonalOut(BGVPersonalIn):
    status: BGVStatus

    class Config:
        from_attributes = True


# ============================================================
# BULK SAVE PAYLOADS
# ============================================================
class BGVEducationsSaveIn(BaseModel):
    candidate_id: str
    educations: List[BGVEducationIn] = Field(default_factory=list)


class BGVEmploymentsSaveIn(BaseModel):
    candidate_id: str
    employments: List[BGVEmploymentIn] = Field(default_factory=list, min_length=1)


class BGVReferencesSaveIn(BaseModel):
    candidate_id: str
    references: List[BGVReferenceIn] = Field(default_factory=list, min_length=3)


# ============================================================
# CHECKLIST
# ============================================================
class BGVChecklistIn(BaseModel):
    candidate_id: str
    has_aadhaar: bool = False
    has_pan: bool = False
    has_passport: bool = False
    has_10_12_grad_pg: bool = False
    has_relieving_letters: bool = False
    has_experience_letters: bool = False
    has_latest_salary_slip: bool = False
    has_bank_statement_3m: bool = False
    has_resume: bool = False


class BGVChecklistOut(BGVChecklistIn):
    status: BGVStatus

    class Config:
        from_attributes = True


# ============================================================
# ADMIN REVIEW
# ============================================================
class BGVAdminReviewIn(BaseModel):
    status: BGVStatus
    admin_remarks: Optional[str] = None


# ============================================================
# SECTION RESPONSES
# ============================================================
class BGVEducationsOut(BaseModel):
    candidate_id: str
    status: BGVStatus
    educations: List[BGVEducationOut]

    class Config:
        from_attributes = True


class BGVEmploymentsOut(BaseModel):
    candidate_id: str
    status: BGVStatus
    employments: List[BGVEmploymentOut]

    class Config:
        from_attributes = True


class BGVReferencesOut(BaseModel):
    candidate_id: str
    status: BGVStatus
    references: List[BGVReferenceOut]

    class Config:
        from_attributes = True


# ============================================================
# FULL BGV PREVIEW (ADMIN / FINAL VIEW)
# ============================================================
class BGVFormOut(BaseModel):
    id: int
    candidate_id: str
    status: BGVStatus

    admin_remarks: Optional[str] = None
    reviewed_by_admin_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime

    full_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    spouse_name: Optional[str] = None

    dob: Optional[date] = None
    gender: Optional[str] = None
    nationality: Optional[str] = None
    marital_status: Optional[str] = None

    aadhaar_number: Optional[str] = None
    pan_number: Optional[str] = None
    uan_number: Optional[str] = None
    provident_fund_number: Optional[str] = None
    passport_number: Optional[str] = None

    permanent_address: Optional[str] = None
    current_address: Optional[str] = None

    contact_number: Optional[str] = None
    alternate_number: Optional[str] = None
    email: Optional[EmailStr] = None

    emergency_contact_number: Optional[str] = None
    emergency_contact_relationship: Optional[EmergencyRelation] = None
    emergency_contact_name: Optional[str] = None

    has_aadhaar: bool
    has_pan: bool
    has_passport: bool
    has_10_12_grad_pg: bool
    has_relieving_letters: bool
    has_experience_letters: bool
    has_latest_salary_slip: bool
    has_bank_statement_3m: bool
    has_resume: bool

    signature_file_key: Optional[str] = None
    signature_mime_type: Optional[str] = None
    signature_size_bytes: Optional[int] = None

    educations: List[BGVEducationOut]
    employments: List[BGVEmploymentOut]
    references: List[BGVReferenceOut]

    class Config:
        from_attributes = True
