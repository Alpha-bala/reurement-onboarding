# app/utils/core/pagination.py
from __future__ import annotations

from typing import Generic, List, TypeVar, Tuple
from pydantic import BaseModel, Field
from sqlalchemy.orm import Query
from sqlalchemy import func

T = TypeVar("T")

# ============================================================
# PAGINATION INPUT (Query Params)
# ============================================================
class PaginationParams(BaseModel):
    """
    Standard pagination query params.
    Use this in all list APIs.
    """
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of records per page (max 100)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Starting index (0-based)",
    )

# ============================================================
# PAGINATED RESPONSE (Frontend friendly)
# ============================================================
class PaginatedResponse(Generic[T], BaseModel):
    items: List[T]
    total: int
    limit: int
    offset: int

# ============================================================
# CORE PAGINATION FUNCTION (SAFE + FAST)
# ============================================================
def paginate(
    query: Query,
    *,
    limit: int,
    offset: int,
) -> Tuple[List[T], int]:
    """
    Apply pagination safely on a SQLAlchemy query.

    - Works with joins
    - Avoids ORDER BY in count()
    - Scales well for large tables

    Returns:
        items  -> paginated ORM objects
        total  -> total row count (without pagination)
    """

    # ---- TOTAL COUNT (safe) ----
    count_q = query.statement.with_only_columns(
        func.count()
    ).order_by(None)

    total = query.session.execute(count_q).scalar_one()

    # ---- PAGINATED DATA ----
    items = (
        query
        .limit(limit)
        .offset(offset)
        .all()
    )

    return items, total
