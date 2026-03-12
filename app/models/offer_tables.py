# app/models/offer_tables.py
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Date,
    Time,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    func,
    Index,
    CheckConstraint,
    TIMESTAMP,
    DATETIME,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ------------------------------------------------------------------
# Candidate-side offer workflow status
# ------------------------------------------------------------------
class OfferCandidateStatus(str, enum.Enum):
    PENDING = "PENDING"
    VIEWED = "VIEWED"
    DOWNLOADED = "DOWNLOADED"
    SIGNED_UPLOADED = "SIGNED_UPLOADED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


# ------------------------------------------------------------------
# PDF Templates
# ------------------------------------------------------------------
class MgrPdfTemplate(Base):
    __tablename__ = "mgr_pdf_templates"

    id = Column(Integer, primary_key=True)
    letter_type = Column(String(32), nullable=False, index=True)
    file_path = Column(Text, nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self):
        return f"<MgrPdfTemplate id={self.id} type={self.letter_type}>"


# ------------------------------------------------------------------
# Offer Letter
# ------------------------------------------------------------------
class MgrOfferLetter(Base):
    __tablename__ = "mgr_offer_letters"

    id = Column(Integer, primary_key=True)

    # keep definition exactly (no DB change)
    candidate_id = Column(String(32), index=True, nullable=False)

    template_id = Column(Integer, ForeignKey("mgr_pdf_templates.id"), nullable=True)

    reference_number = Column(String(64), unique=True, index=True, nullable=False)

    offer_date = Column(Date, nullable=True)
    offer_time = Column(Time, nullable=True)

    annual_ctc = Column(String(64), nullable=True)
    variable_pay = Column(String(64), nullable=True)
    position = Column(String(128), nullable=True)
    designation = Column(String(128), nullable=True)
    band = Column(String(64), nullable=True)
    regime = Column(String(64), nullable=True)

    joining_date = Column(Date, nullable=True)
    acceptance_deadline = Column(Date, nullable=True)

    reporting_time = Column(String(64), nullable=True)
    reporting_person = Column(String(128), nullable=True)
    reporting_office_address = Column(Text, nullable=True)
    reporting_contact_number = Column(String(64), nullable=True)

    co_founder_and_director = Column(String(128), nullable=True)
    office_timings = Column(String(128), nullable=True)
    probation_period = Column(String(64), nullable=True)
    notice_period = Column(String(64), nullable=True)

    token = Column(String(200), nullable=True)
    qr_path = Column(String(200), nullable=True)

    office_name = Column(String(128), nullable=True)
    office_location = Column(String(128), nullable=True)

    pdf_path = Column(Text, nullable=True)

    candidate_status = Column(
        SAEnum(OfferCandidateStatus),
        nullable=False,
        default=OfferCandidateStatus.PENDING,
        index=True,
    )

    viewed_at = Column(DateTime(timezone=True), nullable=True)
    downloaded_at = Column(DateTime(timezone=True), nullable=True)

    signed_pdf_path = Column(Text, nullable=True)
    signed_uploaded_at = Column(DateTime(timezone=True), nullable=True)

    admin_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    admin_review_remark = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    candidate = relationship(
        "CandidateProfileInformation",
        back_populates="offers",
        primaryjoin="foreign(MgrOfferLetter.candidate_id) == CandidateProfileInformation.candidate_id",
        foreign_keys="[MgrOfferLetter.candidate_id]",
        lazy="selectin",
    )

    template = relationship(
        "MgrPdfTemplate",
        lazy="selectin",
    )

    def __repr__(self):
        return (
            f"<MgrOfferLetter ref={self.reference_number} "
            f"status={self.candidate_status}>"
        )


Index(
    "ix_offer_candidate_status",
    MgrOfferLetter.candidate_id,
    MgrOfferLetter.candidate_status,
)


# ------------------------------------------------------------------
# Temporary ID Storage
# ------------------------------------------------------------------
class TemperoryIdStorage(Base):
    __tablename__ = "temporary_id_storage"
    __table_args__ = {"extend_existing": True}

    temporary_id = Column(String(50), primary_key=True, nullable=False)

    status = Column(
        String(20),
        CheckConstraint("status in ('hold', 'closed', 'open')"),
        default="hold",
        nullable=False,
    )

    created_at = Column(
        DATETIME,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<TemporaryId {self.temporary_id} status={self.status}>"
