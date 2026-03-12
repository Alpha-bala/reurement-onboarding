# app/models/screening_model.py
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Float,
    func,
    text,
    Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


# ============== Resume Screening ==============
class ResumeScreening(Base):
    __tablename__ = "resume_screenings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    application_id = Column(
        String(20),
        ForeignKey("applications.application_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

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

    matched_skills = Column(String(500))
    missing_skills = Column(String(500))

    experience_match = Column(
        String(10),
        nullable=False,
        server_default=text("'No'"),
    )

    score = Column(Float, nullable=True)
    status = Column(String(20))  # Shortlisted / Rejected / Hold

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

    # Relationships
    application = relationship(
        "Application",
        back_populates="screenings",
        lazy="selectin",
    )

    job = relationship(
        "Job",
        back_populates="screenings",
        lazy="selectin",
    )

    candidate = relationship(
        "CandidateProfileInformation",
        back_populates="screenings",
        foreign_keys=[candidate_id],
        lazy="selectin",
    )

    def __repr__(self):
        return (
            f"<ResumeScreening id={self.id} "
            f"application_id={self.application_id} "
            f"status={self.status}>"
        )


# Helpful composite index for dashboards & pagination
Index("ix_resume_screening_job_status", ResumeScreening.job_id, ResumeScreening.status)


# ============== Designation ==============
class Designation(Base):
    __tablename__ = "designations"

    designation_id = Column(String(50), primary_key=True, unique=True, index=True)
    designation_name = Column(String(200), nullable=False)
    description = Column(String(200))

    def __repr__(self):
        return f"<Designation id={self.designation_id} name={self.designation_name}>"
