# app/models/user_models.py
from sqlalchemy import (
    Column,
    Integer,
    String,
    Enum,
    Text,
    ForeignKey,
    DateTime,
    Boolean,
    Index,
    CheckConstraint,
    DATE,
    JSON,
    UniqueConstraint,
)
from uuid import uuid4
from sqlalchemy.orm import relationship
from app.database import Base,engine
from datetime import datetime, timezone

# from app.models.bgv import BGVForm
# from app.models.offer_tables import MgrOfferLetter


# ===================== OTP REQUEST =====================
class OTPRequest(Base):
    __tablename__ = "otp_requests"

    otp_id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))

    email = Column(String(100), index=True, nullable=False)

    purpose = Column(
        Enum(
            "signup",
            "reset",
            "forgot",
            "email_change_old",
            "email_change_new",
            name="otp_purpose",
            native_enum=False,
        ),
        nullable=False,
    )

    role = Column(
        Enum("candidate", "admin", "HR", name="otp_role", native_enum=False),
        nullable=False,
        default="candidate",
    )

    otp_hash = Column(String(128), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)

    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_otp_email_purpose_expires", "email", "purpose", "expires_at"),
    )

    def __repr__(self):
        return f"<OTPRequest email={self.email} purpose={self.purpose}>"


# ===================== CANDIDATE PROFILE =====================
class CandidateProfileInformation(Base):
    __tablename__ = "candidate_profile_information"
    __table_args__ = (
        Index("ix_candidate_email", "email"),
        {"extend_existing": True},
    )

    candidate_id = Column(String(50), primary_key=True, nullable=False)
    first_name = Column(String(50), nullable=False)
    last_name = Column(String(50), nullable=False)
    email = Column(String(50), nullable=False)
    contact_number = Column(String(20), nullable=False)
    date_of_birth = Column(DATE, nullable=True)
    gender = Column(String(10), nullable=True)
    address = Column(String(255), nullable=True)
    password = Column(String(100), nullable=False)
    is_active = Column(Boolean, server_default="1", nullable=False)

    highest_qualification = Column(String(50), nullable=True)
    education = Column(JSON, nullable=True, default=dict)
    skills = Column(JSON, nullable=True, default=dict)

    total_experience = Column(String(20), nullable=True)
    relevant_experience = Column(String(20), nullable=True)
    experience = Column(JSON, nullable=True, default=dict)

    documents = relationship(
        "CandidateDocument",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
    )

    applications = relationship(
        "Application",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    interviews = relationship(
        "Interview",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    screenings = relationship(
        "ResumeScreening",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    candidate_documents = relationship(
        "CandidateDocuments",
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        primaryjoin="CandidateProfileInformation.candidate_id == foreign(CandidateDocuments.candidate_id)",
    )

    bgv_form = relationship(
        "BGVForm",
        back_populates="candidate",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        primaryjoin="CandidateProfileInformation.candidate_id == foreign(BGVForm.candidate_id)",
    )

    offers = relationship(
        "MgrOfferLetter",
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        primaryjoin="CandidateProfileInformation.candidate_id == foreign(MgrOfferLetter.candidate_id)",
    )

    candidate_notification_storage = relationship(
        "CandidateNotificationStorage",
        back_populates="candidate_profile_information",
        cascade="all, delete",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<CandidateProfileInformation candidate_id={self.candidate_id}>"


# ===================== CANDIDATE DOCUMENT =====================
class CandidateDocument(Base):
    __tablename__ = "candidate_document"
    __table_args__ = (
        UniqueConstraint("candidate_id", "doc_name", name="uix_candidate_doc"),
        {"extend_existing": True},
    )

    doc_id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(
        String(50),
        ForeignKey("candidate_profile_information.candidate_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    doc_name = Column(String(100), nullable=False)
    doc_sub_name = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(255), nullable=False)
    uploaded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    candidate = relationship(
        "CandidateProfileInformation",
        back_populates="documents",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<CandidateDocument doc_id={self.doc_id} candidate_id={self.candidate_id}>"


# ===================== USER SESSION =====================
class UserSession(Base):
    __tablename__ = "user_sessions"

    session_id = Column(String(100), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(50), nullable=False, index=True)
    role_type = Column(String(20), nullable=False)
    jwt_token = Column(Text, nullable=True)

    login_time = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_activity_time = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    logout_time = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role_type IN ('CANDIDATE','HR','SUPERADMIN','ADMIN')",
            name="ck_user_sessions_role_type",
        ),
        Index("ix_user_sessions_user_active", "user_id", "is_active"),
    )

    def __repr__(self):
        return f"<UserSession user_id={self.user_id} role={self.role_type} active={self.is_active}>"


# ===================== MFA =====================
class MFASecret(Base):
    __tablename__ = "mfa_secrets"

    id = Column(String(50), primary_key=True, default=lambda: str(uuid4()))

    user_id = Column(String(50), nullable=False, index=True)
    role_type = Column(String(20), nullable=False)

    secret_key = Column(String(64), nullable=True)
    backup_codes = Column(JSON, nullable=True, default=list)

    is_verified = Column(Boolean, default=False, nullable=False)
    mfa_enabled = Column(Boolean, default=False)
    enrolled_at = Column(DateTime(timezone=True), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_rotated_at = Column(DateTime(timezone=True), nullable=True)

    failed_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_mfa_user_role", "user_id", "role_type", unique=True),
    )

    def __repr__(self):
        return f"<MFASecret user_id={self.user_id} role={self.role_type} verified={self.is_verified}>"


Base.metadata.create_all(engine)