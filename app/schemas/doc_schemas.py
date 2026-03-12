# app/schemas/doc_schemas.py
from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# DOCUMENT CATALOG (ADMIN CONFIG)
# ============================================================
class DocCatalogBase(BaseModel):
    # Allowed lists
    education: List[str] = Field(default_factory=list)
    id_proof: List[str] = Field(default_factory=list)
    experience: List[str] = Field(default_factory=list)
    medical: List[str] = Field(default_factory=list)

    # Required lists
    required_education: List[str] = Field(default_factory=list)
    required_id_proof: List[str] = Field(default_factory=list)
    required_experience: List[str] = Field(default_factory=list)
    required_medical: List[str] = Field(default_factory=list)

    active: bool = True


class DocCatalogIn(DocCatalogBase):
    pass


class DocCatalogUpdate(BaseModel):
    education: Optional[List[str]] = None
    id_proof: Optional[List[str]] = None
    experience: Optional[List[str]] = None
    medical: Optional[List[str]] = None

    required_education: Optional[List[str]] = None
    required_id_proof: Optional[List[str]] = None
    required_experience: Optional[List[str]] = None
    required_medical: Optional[List[str]] = None

    active: Optional[bool] = None


class DocCatalogOut(DocCatalogBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# CANDIDATE-FACING CATEGORY VIEW
# ============================================================
class CategoryOut(BaseModel):
    """
    Used to render candidate UI:
    EDUCATION / ID_PROOF / EXPERIENCE / MEDICAL
    """
    code: str
    name: str
    items: List[str]


# ============================================================
# OPTIONAL CANDIDATE MINI VIEW
# ============================================================
class CandidateOut(BaseModel):
    """
    Useful when showing candidate details
    next to documents or BGV.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_id: str
    name: str
    email: str
    phone: Optional[str] = None
    qualification: Optional[str] = None
    experience: Optional[str] = None
    status: str


# ============================================================
# DOCUMENT VERSION
# ============================================================
class DocumentVersionOut(BaseModel):
    """Matches app/models/documents.DocumentVersion"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    version_no: int
    status: str
    file_key: str
    file_name: str
    mime_type: str
    size_bytes: int
    reviewer_id: Optional[int] = None
    review_note: Optional[str] = None
    created_at: datetime


# ============================================================
# CANDIDATE DOCUMENT (LATEST STATE)
# ============================================================
class CandidateDocumentOut(BaseModel):
    """Matches app/models/documents.CandidateDocuments"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_id: str
    category_code: str
    sub_type_code: str
    latest_version_no: int
    latest_status: str
    created_at: datetime
    updated_at: datetime


# ============================================================
# DOCUMENT HISTORY VIEW
# ============================================================
class DocumentHistoryOut(BaseModel):
    """
    Used by:
    /candidate/docs/{document_id}/history
    """
    document_id: int
    candidate_id: str
    category_code: str
    sub_type_code: str
    versions: List[DocumentVersionOut]


# ============================================================
# UPLOAD RESPONSE
# ============================================================
class UploadResultOut(BaseModel):
    document_id: int
    version_id: int
    version_no: int
    status: str
