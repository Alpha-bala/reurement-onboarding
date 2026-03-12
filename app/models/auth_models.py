# app/models/auth_models.py
from sqlalchemy import (
    Column,
    String,
    INTEGER,
    ForeignKey,
    JSON,
    DATE,
    Boolean,
    Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


# -----------------------------
# USER ROLE
# -----------------------------
class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = (
        Index("idx_user_role_name", "role_name"),
        {"extend_existing": True},
    )

    role_id = Column(String(50), primary_key=True, nullable=False)
    role_name = Column(String(100), unique=True, nullable=False)
    description = Column(String(255), nullable=True)

    profile_information = relationship(
        "ProfileInformation",
        back_populates="role",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<UserRole role_id={self.role_id} role_name={self.role_name}>"


# -----------------------------
# DEPARTMENT
# -----------------------------
class Department(Base):
    __tablename__ = "department"
    __table_args__ = (
        Index("idx_department_name", "department_name"),
        {"extend_existing": True},
    )

    department_id = Column(String(50), primary_key=True, nullable=False)
    department_name = Column(String(100), unique=True, nullable=False)
    description = Column(String(255), nullable=True)

    profile_information = relationship(
        "ProfileInformation",
        back_populates="department",
        foreign_keys="ProfileInformation.department_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Department department_id={self.department_id} department_name={self.department_name}>"


# -----------------------------
# USER DETAILS
# -----------------------------
class UserDetail(Base):
    __tablename__ = "user_details"
    __table_args__ = (
        Index("idx_user_details_username", "user_name"),
        {"extend_existing": True},
    )

    employee_id = Column(String(50), primary_key=True, nullable=False)
    user_name = Column(String(100), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    is_active = Column(Boolean, server_default="1", nullable=False)

    profile_information = relationship(
        "ProfileInformation",
        back_populates="user_details",
        uselist=False,
        cascade="all, delete",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<UserDetail employee_id={self.employee_id} user_name={self.user_name}>"


# -----------------------------
# PROFILE INFORMATION
# -----------------------------
class ProfileInformation(Base):
    __tablename__ = "profile_information"
    __table_args__ = (
        Index("idx_profile_department_id", "department_id"),
        Index("idx_profile_role_id", "role_id"),
        {"extend_existing": True},
    )

    employee_id = Column(
        String(50),
        ForeignKey("user_details.employee_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(JSON, nullable=True)
    contact_number = Column(JSON, nullable=True)
    emergency_contact_number = Column(JSON, nullable=True)
    gender = Column(String(20), nullable=True)
    date_of_birth = Column(DATE, nullable=True)

    department_id = Column(
        String(50),
        ForeignKey("department.department_id"),
        nullable=True,
    )
    role_id = Column(
        String(50),
        ForeignKey("user_role.role_id"),
        nullable=True,
    )
    hire_date = Column(DATE, nullable=True)

    user_details = relationship(
        "UserDetail",
        back_populates="profile_information",
        lazy="selectin",
    )

    department = relationship(
        "Department",
        back_populates="profile_information",
        foreign_keys=[department_id],
        lazy="selectin",
    )

    role = relationship(
        "UserRole",
        back_populates="profile_information",
        lazy="selectin",
    )

    notification_storage = relationship(
        "NotificationStorage",
        back_populates="profile_information",
        cascade="all, delete",
        passive_deletes=True,
        lazy="selectin",
    )

    jobs_posted = relationship(
        "Job",
        back_populates="posted_by",
        foreign_keys="Job.posted_by_employee_id",
        cascade="save-update, merge",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ProfileInformation employee_id={self.employee_id}>"


# -----------------------------
# EMPLOYEE NOTIFICATIONS
# -----------------------------
class NotificationStorage(Base):
    __tablename__ = "notification_storage"
    __table_args__ = (
        Index("idx_notification_employee_id", "employee_id"),
        {"extend_existing": True},
    )

    notification_id = Column(INTEGER, primary_key=True, nullable=False)
    employee_id = Column(
        String(50),
        ForeignKey("profile_information.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    data = Column(JSON, nullable=False)

    profile_information = relationship(
        "ProfileInformation",
        back_populates="notification_storage",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<NotificationStorage notification_id={self.notification_id}>"


# -----------------------------
# CANDIDATE NOTIFICATIONS
# -----------------------------
class CandidateNotificationStorage(Base):
    __tablename__ = "candidate_notification_storage"
    __table_args__ = (
        Index("idx_candidate_notification_candidate_id", "candidate_id"),
        {"extend_existing": True},
    )

    notification_id = Column(INTEGER, primary_key=True, nullable=False)
    candidate_id = Column(
        String(50),
        ForeignKey("candidate_profile_information.candidate_id"),
        nullable=False,
    )
    data = Column(JSON, nullable=False)

    candidate_profile_information = relationship(
        "CandidateProfileInformation",
        back_populates="candidate_notification_storage",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<CandidateNotificationStorage notification_id={self.notification_id}>"
