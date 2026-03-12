# app/models/documents.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    String,
    Integer,
    ForeignKey,
    DateTime,
    Text,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---- Status constants ----
DOC_STATUS_PENDING   = "PENDING"
DOC_STATUS_APPROVED  = "APPROVED"
DOC_STATUS_REJECTED  = "REJECTED"

BGV_STATUS_SUBMITTED = "SUBMITTED"
BGV_STATUS_APPROVED  = "APPROVED"
BGV_STATUS_REJECTED  = "REJECTED"


# =========================================================
# LOGICAL DOCUMENT (ONE PER CATEGORY + SUBTYPE)
# =========================================================
class CandidateDocuments(Base):
    __tablename__ = "candidate_documents"
    __table_args__ = (
        Index(
            "uq_cand_category_subtype",
            "candidate_id",
            "category_code",
            "sub_type_code",
            unique=True,
        ),
        Index(
            "ix_candidate_docs_candidate_status",
            "candidate_id",
            "latest_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    candidate_id: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("candidate_profile_information.candidate_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    category_code: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    sub_type_code: Mapped[str] = mapped_column(String(80), index=True, nullable=False)

    latest_version_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latest_status: Mapped[str] = mapped_column(
        String(20),
        default=DOC_STATUS_PENDING,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    candidate = relationship(
        "CandidateProfileInformation",
        back_populates="candidate_documents",
        primaryjoin="CandidateProfileInformation.candidate_id == foreign(CandidateDocuments.candidate_id)",
        lazy="selectin",
    )

    versions: Mapped[List["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        order_by="DocumentVersion.version_no.desc()",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self):
        return (
            f"<CandidateDocuments id={self.id} "
            f"candidate_id={self.candidate_id} "
            f"category={self.category_code} "
            f"status={self.latest_status}>"
        )


# =========================================================
# PHYSICAL DOCUMENT VERSION
# =========================================================
class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        Index("ix_doc_versions_document_status", "document_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    document_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    document = relationship(
        "CandidateDocuments",
        back_populates="versions",
        lazy="selectin",
    )

    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20),
        default=DOC_STATUS_PENDING,
        nullable=False,
        index=True,
    )

    reviewer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<DocumentVersion id={self.id} "
            f"doc_id={self.document_id} "
            f"version={self.version_no} "
            f"status={self.status}>"
        )
