# app/routers/doc_config.py
from __future__ import annotations

import json
from typing import List, Optional, Set

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    Body,
    Query,
)
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.database import get_db
from app.models.doc_config import DocCatalog
from app.schemas.doc_schemas import (
    DocCatalogIn,
    DocCatalogOut,
)
from app.utils.guards import hr_session_required


# ===============================================================
# Router Setup — HR + Superadmin only
# ===============================================================
router = APIRouter(
    prefix="/admin/docs",
    tags=["Admin Docs Config"],
    dependencies=[Depends(hr_session_required)],
)

# ===============================================================
# Constants (validated categories)
# ===============================================================
ALLOWED_CATEGORIES: Set[str] = {
    "education",
    "id_proof",
    "experience",
    "medical",
}

ALLOWED_REQUIRED_CATEGORIES: Set[str] = {
    "required_education",
    "required_id_proof",
    "required_experience",
    "required_medical",
}

ALL_ALLOWED_CATEGORIES = ALLOWED_CATEGORIES | ALLOWED_REQUIRED_CATEGORIES


# ===============================================================
# Helper Functions (centralized & safe)
# ===============================================================
def _parse_list(raw: Optional[str]) -> List[str]:
    """Safely parse JSON list from DB string."""
    try:
        data = json.loads(raw or "[]")
        return [x for x in data if isinstance(x, str)]
    except Exception:
        return []


def _dump_list(values: Optional[List[str]]) -> str:
    """Dump list into JSON string."""
    return json.dumps(values or [])


def _validate_category(category: str) -> str:
    category = category.strip()
    if category not in ALL_ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category name")
    return category


def _to_out(model: DocCatalog) -> DocCatalogOut:
    """Convert DB model to response schema."""
    return DocCatalogOut(
        id=model.id,

        education=_parse_list(model.education),
        id_proof=_parse_list(model.id_proof),
        experience=_parse_list(model.experience),
        medical=_parse_list(model.medical),

        required_education=_parse_list(model.required_education),
        required_id_proof=_parse_list(model.required_id_proof),
        required_experience=_parse_list(model.required_experience),
        required_medical=_parse_list(model.required_medical),

        active=model.active,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


# ===============================================================
# Create configuration
# ===============================================================
@router.post("/config", response_model=DocCatalogOut)
def create_config(
    payload: DocCatalogIn,
    db: Session = Depends(get_db),
):
    row = DocCatalog(
        education=_dump_list(payload.education),
        id_proof=_dump_list(payload.id_proof),
        experience=_dump_list(payload.experience),
        medical=_dump_list(payload.medical),

        required_education=_dump_list(payload.required_education),
        required_id_proof=_dump_list(payload.required_id_proof),
        required_experience=_dump_list(payload.required_experience),
        required_medical=_dump_list(payload.required_medical),

        active=payload.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


# ===============================================================
# Get ALL configurations
# ===============================================================
@router.get("/config/all", response_model=List[DocCatalogOut])
def get_all_configs(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(DocCatalog).order_by(desc(DocCatalog.id))
    ).all()

    if not rows:
        raise HTTPException(status_code=404, detail="No configurations found")

    return [_to_out(r) for r in rows]


# ===============================================================
# Get latest configuration
# ===============================================================
@router.get("/config", response_model=DocCatalogOut)
def get_latest_config(db: Session = Depends(get_db)):
    row = db.scalar(
        select(DocCatalog).order_by(desc(DocCatalog.id))
    )
    if not row:
        raise HTTPException(status_code=404, detail="No configuration found")

    return _to_out(row)


# ===============================================================
# Update a single item inside category
# ===============================================================
@router.patch("/config/{config_id}/update-item", response_model=DocCatalogOut)
def update_item(
    config_id: int,
    category: str,
    old_item: str,
    new_item: str,
    db: Session = Depends(get_db),
):
    category = _validate_category(category)

    row = db.get(DocCatalog, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    items = _parse_list(getattr(row, category))

    if old_item not in items:
        raise HTTPException(status_code=404, detail=f"Item '{old_item}' not found")
    if new_item in items:
        raise HTTPException(status_code=400, detail=f"Item '{new_item}' already exists")

    items[items.index(old_item)] = new_item
    setattr(row, category, _dump_list(items))

    db.commit()
    db.refresh(row)
    return _to_out(row)


# ===============================================================
# Add item(s) inside category
# ===============================================================
@router.patch("/config/{config_id}/add-item", response_model=DocCatalogOut)
def add_item(
    config_id: int,
    category: str,
    item: Optional[str] = None,
    items: Optional[List[str]] = Body(default=None),
    db: Session = Depends(get_db),
):
    category = _validate_category(category)

    row = db.get(DocCatalog, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    existing = _parse_list(getattr(row, category))

    new_items: List[str] = []
    if item:
        new_items.append(item)
    if items:
        new_items.extend(items)

    if not new_items:
        raise HTTPException(status_code=400, detail="Provide 'item' or 'items'")

    for x in new_items:
        if x in existing:
            raise HTTPException(status_code=400, detail=f"'{x}' already exists")
        existing.append(x)

    setattr(row, category, _dump_list(existing))

    db.commit()
    db.refresh(row)
    return _to_out(row)


# ===============================================================
# Remove item
# ===============================================================
@router.patch("/config/{config_id}/remove-item", response_model=DocCatalogOut)
def remove_item(
    config_id: int,
    category: str,
    item: str,
    db: Session = Depends(get_db),
):
    category = _validate_category(category)

    row = db.get(DocCatalog, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    items = _parse_list(getattr(row, category))

    if item not in items:
        raise HTTPException(status_code=404, detail="Item not found")

    items.remove(item)
    setattr(row, category, _dump_list(items))

    db.commit()
    db.refresh(row)
    return _to_out(row)


# ===============================================================
# FULL UPDATE — Update entire config using Query params
# ===============================================================
@router.put("/config/{config_id}", response_model=DocCatalogOut)
def update_config(
    config_id: int,
    education: Optional[List[str]] = Query(default=None),
    id_proof: Optional[List[str]] = Query(default=None),
    experience: Optional[List[str]] = Query(default=None),
    medical: Optional[List[str]] = Query(default=None),
    required_education: Optional[List[str]] = Query(default=None),
    required_id_proof: Optional[List[str]] = Query(default=None),
    required_experience: Optional[List[str]] = Query(default=None),
    required_medical: Optional[List[str]] = Query(default=None),
    active: Optional[bool] = Query(default=None),
    db: Session = Depends(get_db),
):
    row = db.get(DocCatalog, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    updates = {
        "education": education,
        "id_proof": id_proof,
        "experience": experience,
        "medical": medical,
        "required_education": required_education,
        "required_id_proof": required_id_proof,
        "required_experience": required_experience,
        "required_medical": required_medical,
    }

    for field, value in updates.items():
        if value is not None:
            setattr(row, field, _dump_list(value))

    if active is not None:
        row.active = active

    db.commit()
    db.refresh(row)
    return _to_out(row)


# ===============================================================
# Delete configuration
# ===============================================================
@router.delete("/config/{config_id}", status_code=status.HTTP_200_OK)
def delete_config(
    config_id: int,
    db: Session = Depends(get_db),
):
    row = db.get(DocCatalog, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    db.delete(row)
    db.commit()

    return {
        "message": "Config deleted successfully",
        "config_id": config_id,
    }
