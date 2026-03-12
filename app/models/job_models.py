# app/models/job_models.py
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, Time, ForeignKey,
    Index, func, text, Boolean, SmallInteger, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy import Enum as SAEnum
from datetime import datetime, timezone
from uuid import uuid4

from app.database import Base

# =========================
# JOB
# =========================
class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(String(10), primary_key=True, index=True)

    posted_by_employee_id = Column(
        String(50),
        ForeignKey("profile_information.employee_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    company_id = Column(
        String(50),
        ForeignKey("companies.company_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = Column(String(200), nullable=False)
    job_type = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    work_mode = Column(String(255), nullable=False)

    responsibilities = Column(Text, nullable=False)
    primary_skills = Column(Text, nullable=False)
    secondary_skills = Column(Text, nullable=True)
    qualification_requirement = Column(Text, nullable=False)
    salary = Column(String(100), nullable=True)

    total_experience = Column(String(50), nullable=False)
    relevant_experience = Column(String(100), nullable=True)
    number_of_positions = Column(Integer, default=1, nullable=False)

    category = Column(String(100), nullable=True, index=True)
    location = Column(String(200), nullable=False)
    designation = Column(String(100), nullable=False)
    band = Column(String(100), nullable=False)

    date_posted = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    application_deadline = Column(Date, nullable=True)

    status = Column(String(20), default="Open", nullable=False, index=True)

    applications = relationship(
        "Application",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    screenings = relationship(
        "ResumeScreening",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    interviews = relationship(
        "Interview",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    posted_by = relationship(
        "ProfileInformation",
        back_populates="jobs_posted",
        foreign_keys=[posted_by_employee_id],
        lazy="selectin",
    )

    company = relationship(
        "Company",
        back_populates="jobs",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Job job_id={self.job_id} status={self.status}>"


Index("ix_jobs_status_postedby", Job.status, Job.posted_by_employee_id)
Index("ix_jobs_company_status", Job.company_id, Job.status)

# =========================
# COMPANY
# =========================
class Company(Base):
    __tablename__ = "companies"

    company_id = Column(String(50), primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    logo_url = Column(String(500), nullable=True)
    location = Column(String(255), nullable=True)
    website = Column(String(255), nullable=True)
    industry = Column(String(100), nullable=True)
    about = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    jobs = relationship(
        "Job",
        back_populates="company",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Company company_id={self.company_id} name={self.name}>"

# =========================
# SAVED JOB
# =========================
class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id = Column(String(50), primary_key=True, index=True)
    candidate_id = Column(
        String(50),
        ForeignKey("candidate_profile_information.candidate_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id = Column(
        String(10),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    saved_on = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<SavedJob candidate_id={self.candidate_id} job_id={self.job_id}>"

# =========================
# APPLICATION
# =========================
class ApplicationSource(str, PyEnum):
    socialmedia = "socialmedia"
    linkedin = "linkedin"
    naukri = "naukri"
    internal_employee = "internal_employee"


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(String(20), unique=True, index=True)

    candidate_id = Column(
        String(50),
        ForeignKey("candidate_profile_information.candidate_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    employee_id = Column(
        String(100),
        ForeignKey("profile_information.employee_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    job_id = Column(
        String(10),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source = Column(
        SAEnum(ApplicationSource, name="application_source_enum"),
        nullable=False,
        index=True,
    )

    status = Column(String(50), default="applied", index=True)
    applied_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    candidate = relationship(
        "CandidateProfileInformation",
        back_populates="applications",
        lazy="selectin",
    )

    job = relationship(
        "Job",
        back_populates="applications",
        lazy="selectin",
    )

    screenings = relationship(
        "ResumeScreening",
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Application application_id={self.application_id} status={self.status}>"

# =========================
# INTERVIEW ENUMS
# =========================
class ExperienceType(str, PyEnum):
    fresher = "fresher"
    experienced = "experienced"

class InterviewType(str, PyEnum):
    virtual = "virtual"
    physical = "physical"

class RoundStatus(str, PyEnum):
    pending = "pending"
    rescheduled = "rescheduled"
    cancelled = "cancelled"
    no_show = "no_show"
    passed = "passed"
    failed = "failed"

class RoundDecision(str, PyEnum):
    proceed = "proceed"
    hold = "hold"
    reject = "reject"

# =========================
# INTERVIEW
# =========================
class Interview(Base):
    __tablename__ = "interviews"

    interview_id = Column(Integer, primary_key=True, autoincrement=True)
    unique_id = Column(String(100), unique=True, default=lambda: str(uuid4()))

    candidate_id = Column(
        String(50),
        ForeignKey("candidate_profile_information.candidate_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_id = Column(
        String(10),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    candidate_first_name = Column(String(80), nullable=False, index=True)
    candidate_last_name = Column(String(80), nullable=False, index=True)
    candidate_email = Column(String(120), nullable=False, index=True)

    experience_type = Column(
        SAEnum(ExperienceType, name="experience_type_enum"),
        nullable=False,
        index=True,
    )

    total_experience_years = Column(String(30), nullable=True)
    relevant_experience_years = Column(String(30), nullable=True)

    technology = Column(String(100), nullable=False, index=True)
    level_number = Column(Integer, nullable=False, index=True)
    round_category = Column(String(30), nullable=False,
                            default="technical", server_default=text("'technical'"))
    round_name = Column(String(80), nullable=False, index=True)

    date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    interview_timezone = Column(
        String(50), nullable=False,
        default="Asia/Kolkata", server_default=text("'Asia/Kolkata'")
    )

    interview_type = Column(
        SAEnum(InterviewType, name="interview_type_enum"),
        nullable=False,
        default=InterviewType.virtual,
        server_default=text("'virtual'"),
        index=True,
    )

    interview_link = Column(String(255), nullable=True)
    location_full = Column(String(255), nullable=True)

    panel_members = Column(JSON, nullable=False, default=list)

    actual_start_at = Column(DateTime(timezone=True), nullable=True)
    actual_end_at = Column(DateTime(timezone=True), nullable=True)

    attended = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    feedback_text = Column(String(2000), nullable=True)
    feedback_rating = Column(SmallInteger, nullable=True)
    decision = Column(SAEnum(RoundDecision, name="round_decision_enum"), nullable=True)

    evaluated_by_name = Column(String(120), nullable=True)
    evaluated_by_email = Column(String(120), nullable=True, index=True)
    evaluated_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(
        SAEnum(RoundStatus, name="round_status_enum"),
        default=RoundStatus.pending,
        server_default=text("'pending'"),
        nullable=False,
        index=True,
    )

    reason = Column(String(255), nullable=True)

    created_by = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    job = relationship("Job", back_populates="interviews", lazy="selectin")
    candidate = relationship(
        "CandidateProfileInformation",
        back_populates="interviews",
        lazy="selectin"
    )

    feedback_submissions = relationship(
        "InterviewFeedback",
        back_populates="interview",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Interview interview_id={self.interview_id} status={self.status}>"

# =========================
# INTERVIEW FEEDBACK
# =========================
class InterviewFeedback(Base):
    __tablename__ = "interview_feedbacks"

    feedback_id = Column(Integer, primary_key=True, autoincrement=True)
    interview_id = Column(
        Integer,
        ForeignKey("interviews.interview_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    panel_member_name = Column(String(120), nullable=False)
    panel_member_email = Column(String(120), nullable=False, index=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    attended = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    questions_asked = Column(JSON, nullable=True)
    feedback_text = Column(String(2000), nullable=True)
    feedback_rating = Column(SmallInteger, nullable=True)
    decision = Column(SAEnum(RoundDecision, name="round_decision_enum"), nullable=True)
    reason = Column(String(255), nullable=True)

    approved = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    approved_by_email = Column(String(120), nullable=True, index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    interview = relationship(
        "Interview",
        back_populates="feedback_submissions",
        lazy="selectin",
    )
