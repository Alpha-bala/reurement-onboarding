# app/models/doc_config.py
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Integer, Boolean, DateTime, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocCatalog(Base):
    __tablename__ = "doc_catalog"
    __table_args__ = (
        Index("ix_doc_catalog_active", "active"),
        Index("ix_doc_catalog_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Allowable subtypes for candidates
    education: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_proof: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    medical: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Required subtypes (subset of allowed)
    required_education: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_id_proof: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_medical: Mapped[str | None] = mapped_column(Text, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
        return f"<DocCatalog id={self.id} active={self.active}>"
