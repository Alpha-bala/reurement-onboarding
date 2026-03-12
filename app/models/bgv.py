# app/models/bgv.py
from __future__ import annotations

from datetime import datetime, date, timezone
from app.models.user_models import CandidateProfileInformation
from typing import List, Optional, TypedDict
import enum

from sqlalchemy import (
    String,
    Integer,
    ForeignKey,
    Date,
    DateTime,
    Enum,
    Boolean,
    Text,
    JSON,
    Index,
    func,
)
from sqlalchemy.orm import (
    relationship as sa_relationship,
    Mapped,
    mapped_column,
)

from app.database import Base


# =========================
# ENUMS
# =========================
class BGVStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EmergencyRelation(str, enum.Enum):
    SPOUSE = "SPOUSE"
    FATHER = "FATHER"
    MOTHER = "MOTHER"
    OTHER = "OTHER"


# =========================
# BGV FORM
# =========================
class BGVForm(Base):
    __tablename__ = "bgv_forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    candidate_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("candidate_profile_information.candidate_id"),
        index=True,
        nullable=False,
    )

    full_name: Mapped[Optional[str]] = mapped_column(String(128))
    father_name: Mapped[Optional[str]] = mapped_column(String(128))
    mother_name: Mapped[Optional[str]] = mapped_column(String(128))
    spouse_name: Mapped[Optional[str]] = mapped_column(String(128))

    dob: Mapped[Optional[date]] = mapped_column(Date)
    gender: Mapped[Optional[str]] = mapped_column(String(16))
    nationality: Mapped[Optional[str]] = mapped_column(String(64))
    marital_status: Mapped[Optional[str]] = mapped_column(String(32))
    aadhaar_number: Mapped[Optional[str]] = mapped_column(String(20))
    pan_number: Mapped[Optional[str]] = mapped_column(String(20))
    uan_number: Mapped[Optional[str]] = mapped_column(String(32))
    passport_number: Mapped[Optional[str]] = mapped_column(String(20))
    provident_fund_number: Mapped[Optional[str]] = mapped_column(String(32))

    permanent_address: Mapped[Optional[str]] = mapped_column(String(255))
    current_address: Mapped[Optional[str]] = mapped_column(String(255))
    contact_number: Mapped[Optional[str]] = mapped_column(String(20))
    alternate_number: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(120))

    emergency_contact_relationship: Mapped[Optional[EmergencyRelation]] = mapped_column(
        Enum(EmergencyRelation),
        nullable=True,
    )
    emergency_contact_name: Mapped[Optional[str]] = mapped_column(String(128))
    emergency_contact_number: Mapped[Optional[str]] = mapped_column(String(20))

    has_aadhaar: Mapped[bool] = mapped_column(Boolean, default=False)
    has_pan: Mapped[bool] = mapped_column(Boolean, default=False)
    has_passport: Mapped[bool] = mapped_column(Boolean, default=False)
    has_10_12_grad_pg: Mapped[bool] = mapped_column(Boolean, default=False)
    has_relieving_letters: Mapped[bool] = mapped_column(Boolean, default=False)
    has_experience_letters: Mapped[bool] = mapped_column(Boolean, default=False)
    has_latest_salary_slip: Mapped[bool] = mapped_column(Boolean, default=False)
    has_bank_statement_3m: Mapped[bool] = mapped_column(Boolean, default=False)
    has_resume: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[BGVStatus] = mapped_column(
        Enum(BGVStatus),
        default=BGVStatus.PENDING,
        nullable=False,
        index=True,
    )

    admin_remarks: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by_admin_id: Mapped[Optional[str]] = mapped_column(String(32))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    signature_file_key: Mapped[Optional[str]] = mapped_column(String(512))
    signature_mime_type: Mapped[Optional[str]] = mapped_column(String(64))
    signature_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    candidate: Mapped["CandidateProfileInformation"] = sa_relationship(
        "CandidateProfileInformation",
        primaryjoin="CandidateProfileInformation.candidate_id == foreign(BGVForm.candidate_id)",
        back_populates="bgv_form",
        uselist=False,
        lazy="selectin",
    )

    educations: Mapped[List["BGVEducation"]] = sa_relationship(
        "BGVEducation",
        back_populates="form",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    employments: Mapped[List["BGVEmployment"]] = sa_relationship(
        "BGVEmployment",
        back_populates="form",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    references: Mapped[List["BGVReference"]] = sa_relationship(
        "BGVReference",
        back_populates="form",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self):
        return f"<BGVForm id={self.id} status={self.status}>"


Index("ix_bgv_candidate_status", BGVForm.candidate_id, BGVForm.status)


# =========================
# EDUCATION
# =========================
class BGVEducation(Base):
    __tablename__ = "bgv_educations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    form_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bgv_forms.id", ondelete="CASCADE"),
        index=True,
    )

    qualification: Mapped[Optional[str]] = mapped_column(String(80))
    institution_name: Mapped[Optional[str]] = mapped_column(String(160))
    university_or_board: Mapped[Optional[str]] = mapped_column(String(160))
    year_of_passing: Mapped[Optional[int]] = mapped_column(Integer)
    percentage_or_cgpa: Mapped[Optional[str]] = mapped_column(String(32))

    form: Mapped["BGVForm"] = sa_relationship(
        "BGVForm",
        back_populates="educations",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<BGVEducation id={self.id}>"


# =========================
# EMPLOYMENT
# =========================
class EmploymentVerifierJSON(TypedDict):
    name: str
    designation: str
    contact_number: str


class BGVEmployment(Base):
    __tablename__ = "bgv_employments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    form_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bgv_forms.id", ondelete="CASCADE"),
        index=True,
    )

    company_name: Mapped[Optional[str]] = mapped_column(String(160))
    designation: Mapped[Optional[str]] = mapped_column(String(120))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    reason_for_leaving: Mapped[Optional[str]] = mapped_column(String(255))

    verifiers: Mapped[Optional[List[EmploymentVerifierJSON]]] = mapped_column(
        JSON,
        nullable=True,
    )

    form: Mapped["BGVForm"] = sa_relationship(
        "BGVForm",
        back_populates="employments",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<BGVEmployment id={self.id}>"


# =========================
# REFERENCES
# =========================
class BGVReference(Base):
    __tablename__ = "bgv_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    form_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bgv_forms.id", ondelete="CASCADE"),
        index=True,
    )

    name: Mapped[Optional[str]] = mapped_column(String(120))
    designation: Mapped[Optional[str]] = mapped_column(String(120))
    company: Mapped[Optional[str]] = mapped_column(String(160))
    relationship: Mapped[Optional[str]] = mapped_column(String(80))
    contact_number: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(120))

    form: Mapped["BGVForm"] = sa_relationship(
        "BGVForm",
        back_populates="references",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<BGVReference id={self.id}>"
