# app/routers/candidate_documents.py
from __future__ import annotations
import json
import os
import re
from enum import Enum
import tempfile
import zipfile
import boto3
from datetime import datetime
from botocore.exceptions import ClientError
from typing import List, Dict, Any, Optional
from typing_extensions import Annotated
from contextlib import contextmanager
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Form,
    Query,
    BackgroundTasks,
)
from fastapi.responses import FileResponse
from sqlalchemy import select, func, desc, and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db, SessionLocal
from app.models.job_models import Job
from app.models.user_models import CandidateProfileInformation as DBCandidate
from app.models.doc_config import DocCatalog
from app.models.documents import (
    CandidateDocuments,
    DocumentVersion,
    DOC_STATUS_PENDING,
    DOC_STATUS_APPROVED,
)
from app.schemas.doc_schemas import (
    UploadResultOut,
    CandidateDocumentOut,
    DocumentVersionOut,
    DocumentHistoryOut,
)
from app.schemas.user_schemas import NotificationStructure
from app.utils.s3_utils import _upload_to_s3, generate_http_url
from app.utils.notify_depends import send_notification_to_employee
from app.utils.guards import candidate_session_required

# ===============================================================
# Router Setup — Candidate Only + Active Session Validation
# ===============================================================
router = APIRouter(
    prefix="/candidate",
    tags=["Candidate Documents & BGV"],
    dependencies=[Depends(candidate_session_required)],
)

# ----------------------- Configuration -----------------------
STRICT_SUBTYPE_VALIDATION = True
ENFORCE_DOCS_BEFORE_BGV = False

# ----------------------- Status constants -----------------------
DOC_STATUS_REFERRED_BACK = "REFERRED_BACK"
DOC_STATUS_REJECTED = "REJECTED"
DOC_STATUS_SUBMITTED = "SUBMITTED"

MAX_REFER_BACKS_ALLOWED = 3

# ----------------------- Categories -------------------------
class CategoryEnum(str, Enum):
    EDUCATION = "EDUCATION"
    ID_PROOF = "ID_PROOF"
    EXPERIENCE = "EXPERIENCE"
    MEDICAL = "MEDICAL"

# ----------------------- Defaults -----------------------
DEFAULT_MAPPING: Dict[str, List[str]] = {
    "EDUCATION": ["TENTH", "INTERMEDIATE", "DEGREE", "PG", "OTHER"],
    "ID_PROOF": ["AADHAR", "PAN", "PASSPORT", "OTHER"],
    "EXPERIENCE": [
        "EXPERIENCE LETTER",
        "RELIEVING LETTER",
        "LAST 3 MONTH PAYSLIPS",
        "FORM 16",
        "RECENT HIKE LETTER",
        "OFFER LETTER",
        "OTHER",
    ],
    "MEDICAL": ["ANY MEDICAL DOCUMENTS"],
}

# ----------------------- File validation rules -----------------------
ALLOWED_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_MIMES = {"application/pdf", "image/jpeg", "image/png"}
MAX_SIZE_MB = 15
MAX_VERSIONS = 5


# ----------------------- Application shortlist guard -----------------------
def _ensure_application_shortlisted(db: Session, candidate_public_id: str) -> None:
    """
    Ensure the candidate has at least one Application with status == 'passed'.
    Raises HTTP 403 if not.
    """
    # Lazy import to avoid circular imports at module import time
    from app.models.job_models import Application

    application = db.scalar(
        select(Application).where(
            Application.candidate_id == candidate_public_id,
            func.upper(Application.status) == "passed"
        ).order_by(desc(Application.applied_at))
    )

    if not application:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: document operations allowed only for candidates with application status 'SHORTLISTED'."
        )

# ----------------------- Helpers -----------------------
def _sanitize_path_component(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


from contextlib import contextmanager
from typing import Iterator
from sqlalchemy.orm import Session

@contextmanager
def _db_once() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()



def _ensure_candidate_exists(db: Session, candidate_public_id: str) -> None:
    row = db.scalar(select(DBCandidate).where(DBCandidate.candidate_id == candidate_public_id))
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")

def _json_list(s: str | None) -> list[str]:
    try:
        return [x.strip() for x in (json.loads(s or "[]")) if isinstance(x, str)]
    except Exception:
        return []

SUBTYPE_FIXES: Dict[str, str] = {
    "10TH": "TENTH",
    "SSC": "TENTH",
    "INTER": "INTERMEDIATE",
    "AADHAAR": "AADHAR",
    "AADHAR CARD": "AADHAR",
    "PAN CARD": "PAN",
    "OFFER LETTER": "OFFER LETTER",
    "EXPERIENCE LETTER": "EXPERIENCE LETTER",
    "RELIEVING": "RELIEVING LETTER",
    "PAY SLIPS": "LAST 3 MONTH PAYSLIPS",
    "PAYSLICPS": "LAST 3 MONTH PAYSLIPS",
    "FORM-16": "FORM 16",
}

def _canonicalize_subtype(sub: str) -> str:
    """Normalize subtype names to canonical form."""
    s = (sub or "").strip().upper()
    return SUBTYPE_FIXES.get(s, s)

def _latest_catalog(db: Session) -> DocCatalog | None:
    """Get the latest active document catalog."""
    try:
        row = db.scalar(
            select(DocCatalog)
            .where(DocCatalog.active == True)
            .order_by(desc(DocCatalog.id))
        ) or db.scalar(select(DocCatalog).order_by(desc(DocCatalog.id)))
    except SQLAlchemyError:
        row = None
    return row

def _effective_mapping(db: Session) -> Dict[str, List[str]]:
    """
    Build the effective category → subtypes mapping dynamically from DocCatalog.
    Falls back to DEFAULT_MAPPING when admin has not configured values.
    """
    row = _latest_catalog(db)
    if not row:
        return DEFAULT_MAPPING

    def norm_list(raw: str | None) -> List[str]:
        """Normalize and convert JSON string → List[str]."""
        return sorted({_canonicalize_subtype(s) for s in _json_list(raw)})

    return {
        "EDUCATION": norm_list(row.education) or DEFAULT_MAPPING["EDUCATION"],
        "ID_PROOF": norm_list(row.id_proof) or DEFAULT_MAPPING["ID_PROOF"],
        "EXPERIENCE": norm_list(row.experience) or DEFAULT_MAPPING["EXPERIENCE"],
        "MEDICAL": norm_list(row.medical) or DEFAULT_MAPPING["MEDICAL"],
    }

# ---------- Category-specific subtype enums ----------
def _build_subtype_enum_for(category_code: str) -> type[Enum]:
    """Dynamically create subtype enum for each document category."""
    with _db_once() as db:
        try:
            allowed = _effective_mapping(db).get(category_code, [])
        except Exception:
            allowed = []
    if not allowed:
        return Enum(f"SubType{category_code.title().replace('_', '')}Enum", {"PLACEHOLDER": "PLACEHOLDER"})
    taken: set[str] = set()

    def _name(v: str) -> str:
        name = re.sub(r"[^A-Za-z0-9]+", "_", v).strip("_") or "ITEM"
        if name and name[0].isdigit():
            name = f"{name}_"
        base = name
        i = 2
        while name in taken:
            name = f"{base}_{i}"
            i += 1
        taken.add(name)
        return name

    return Enum(
        f"SubType{category_code.title().replace('_', '')}Enum",
        {_name(v): v for v in allowed}
    )


SubTypeEducationEnum = _build_subtype_enum_for("EDUCATION")
SubTypeIdProofEnum = _build_subtype_enum_for("ID_PROOF")
SubTypeExperienceEnum = _build_subtype_enum_for("EXPERIENCE")
SubTypeMedicalEnum = _build_subtype_enum_for("MEDICAL")


# ----------------------- Readiness rules/check (Dynamic) -----------------------
def _required_rules(db: Session) -> Dict[str, Dict[str, List[str]]]:
    """
    Build dynamic REQUIRED_RULES from DocCatalog.
    Falls back to defaults if admin has not configured required lists.
    """
    row = _latest_catalog(db)

    if not row:
        return {
            "EDUCATION": {"all_of": DEFAULT_MAPPING["EDUCATION"]},
            "ID_PROOF": {"all_of": ["AADHAR", "PAN"]},
            "EXPERIENCE": {"one_of": DEFAULT_MAPPING["EXPERIENCE"]},
        }

    return {
        "EDUCATION": {"all_of": _json_list(row.required_education) or DEFAULT_MAPPING["EDUCATION"]},
        "ID_PROOF": {"all_of": _json_list(row.required_id_proof) or ["AADHAR", "PAN"]},
        "EXPERIENCE": {"one_of": _json_list(row.required_experience) or DEFAULT_MAPPING["EXPERIENCE"]},
        # MEDICAL → optional, no required rule
    }


def _doc_status(db: Session, cand_pubid: str, category: str, sub_type: str) -> str:
    """Get document status by category and subtype."""
    sub_type = _canonicalize_subtype(sub_type)
    row = db.scalar(
        select(CandidateDocuments).where(
            CandidateDocuments.candidate_id == cand_pubid,
            CandidateDocuments.category_code == category,
            CandidateDocuments.sub_type_code == sub_type,
        )
    )
    return (row.latest_status or "NOT_UPLOADED") if row else "NOT_UPLOADED"


def _compute_readiness(db: Session, cand_pubid: str) -> Dict[str, Any]:
    """Compute readiness dynamically using required rules."""
    missing: List[Dict[str, str]] = []
    rules = _required_rules(db)

    # Allowed upload statuses before submission
    ALLOWED_STATUSES = ["PENDING", "SUBMITTED", "APPROVED", "REFERRED_BACK"]

    for cat, rule in rules.items():
        if "all_of" in rule:
            for sub in rule["all_of"]:
                st = _doc_status(db, cand_pubid, cat, sub)
                if st not in ALLOWED_STATUSES:
                    missing.append({
                        "category_code": cat,
                        "sub_type_code": sub,
                        "current_status": st,
                        "needed": "PENDING or SUBMITTED or APPROVED or REFERRED_BACK",
                    })

        elif "one_of" in rule:
            options = rule["one_of"]
            statuses = [_doc_status(db, cand_pubid, cat, s) for s in options]

            if not any(s in ALLOWED_STATUSES for s in statuses):
                missing.append({
                    "category_code": cat,
                    "sub_type_code": " / ".join(options),
                    "current_status": " / ".join(statuses),
                    "needed": "PENDING or SUBMITTED or APPROVED or REFERRED_BACK (any one)",
                })

    return {"missing_required": missing, "ready": len(missing) == 0}


# ----------------------- Resolve candidate id -----------------------
def _resolve_candidate_id(token_sub: str, provided: Optional[str]) -> str:
    """Resolve candidate_id (either from token or form param)."""
    token_sub_u = (token_sub or "").strip().upper()
    prov_u = (provided or "").strip().upper()
    if not prov_u or prov_u == "STRING":
        return token_sub_u
    if prov_u != token_sub_u:
        raise HTTPException(status_code=403, detail="Forbidden for this candidate_id")
    return prov_u


# ----------------------- Version/status helpers -----------------------
def _count_referred_backs(db: Session, doc_id: int) -> int:
    """Count number of referred-backs for a document."""
    return db.scalar(
        select(func.count()).select_from(DocumentVersion).where(
            and_(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.status == DOC_STATUS_REFERRED_BACK,
            )
        )
    ) or 0


def _ensure_can_reupload(doc: CandidateDocuments, db: Session) -> None:
    """Business rules to allow reupload."""
    latest = (doc.latest_status or "").upper()

    if latest == DOC_STATUS_REJECTED:
        raise HTTPException(
            409, "Cannot re-upload: document is REJECTED. Candidate-side re-upload is disabled."
        )

    if latest not in [DOC_STATUS_REFERRED_BACK, DOC_STATUS_PENDING]:
        raise HTTPException(
            409,
            f"Cannot re-upload: latest status is '{doc.latest_status}'. Expected '{DOC_STATUS_REFERRED_BACK}' or '{DOC_STATUS_PENDING}'.",
        )

    rb_count = _count_referred_backs(db, doc.id)
    if rb_count >= MAX_REFER_BACKS_ALLOWED:
        doc.latest_status = DOC_STATUS_REJECTED
        try:
            db.flush()
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(
            409,
            f"Cannot re-upload: document auto-rejected after {MAX_REFER_BACKS_ALLOWED} referred-backs.",
        )


# =============================================================================
# Notification helper
# =============================================================================
def _notify_hr_application_sync(employee_id: str, payload: dict) -> None:
    """Send async notification to HR."""
    import asyncio
    try:
        asyncio.run(send_notification_to_employee(employee_id, payload))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(send_notification_to_employee(employee_id, payload))


# ============================== Core upload helper ==============================
async def _handle_upload_core(
        db: Session,
        candidate_public_id: str,
        category_code: str,
        sub_type_code: str,
        file: UploadFile,
        background_tasks: Optional[BackgroundTasks] = None,
) -> UploadResultOut:
    """Handles initial document uploads to S3 and database."""
    cat_code = category_code.strip().upper()
    sub_code = _canonicalize_subtype(sub_type_code)

    if STRICT_SUBTYPE_VALIDATION:
        allowed = set(_effective_mapping(db).get(cat_code, []))
        if sub_code not in allowed:
            raise HTTPException(
                400,
                f"sub_type_code '{sub_code}' not allowed for {cat_code}. Allowed: {sorted(allowed)}",
            )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Extension '{ext}' not allowed")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIMES:
        raise HTTPException(400, "MIME type not allowed")

    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        raise HTTPException(400, f"File too large: {size_mb:.2f} MB > {MAX_SIZE_MB} MB")
    await file.seek(0)

    doc = db.scalar(
        select(CandidateDocuments).where(
            CandidateDocuments.candidate_id == candidate_public_id,
            CandidateDocuments.category_code == cat_code,
            CandidateDocuments.sub_type_code == sub_code,
        )
    )
    if not doc:
        doc = CandidateDocuments(
            candidate_id=candidate_public_id,
            category_code=cat_code,
            sub_type_code=sub_code,
        )
        db.add(doc)
        db.flush()

    current_max = (
            db.scalar(
                select(func.coalesce(func.max(DocumentVersion.version_no), 0)).where(
                    DocumentVersion.document_id == doc.id
                )
            )
            or 0
    )
    next_ver = current_max + 1
    if next_ver > MAX_VERSIONS:
        raise HTTPException(400, f"Max versions ({MAX_VERSIONS}) reached")

    # ------ S3 Integration FIXED ----------
    s3_key = (
        f"{candidate_public_id}/{cat_code}_{sub_code}/v{next_ver}/{_sanitize_path_component(file.filename)}"
    )

    # Upload → returns s3://bucket/key
    s3_uri = _upload_to_s3(
        file_bytes=contents,
        key=s3_key,
        content_type=file.content_type or "application/octet-stream",
    )

    # Convert to HTTP/CloudFront/Presigned URL
    url = generate_http_url(s3_uri)

    print("S3 URI STORED:", s3_uri)
    print("HTTP URL GENERATED:", url)

    ver = DocumentVersion(
        document_id=doc.id,
        version_no=next_ver,
        file_key=url,  # store actual URL
        file_name=file.filename or f"file{ext}",
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
        status=DOC_STATUS_PENDING,
    )
    db.add(ver)

    doc.latest_version_no = max(doc.latest_version_no or 0, next_ver)
    doc.latest_status = DOC_STATUS_PENDING

    try:
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Upload failed")

    # ============================
    # CORRECT HR LOOKUP LOGIC
    # ============================
    from app.models.job_models import Application

    application = db.scalar(
        select(Application)
        .where(Application.candidate_id == candidate_public_id)
        .order_by(desc(Application.applied_at))
    )

    hr_employee_id = ""

    if application:
        job = db.scalar(select(Job).where(Job.job_id == application.job_id))
        if job and job.posted_by_employee_id:
            hr_employee_id = str(job.posted_by_employee_id).strip()

    # === Send notification ===
    if hr_employee_id:
        notif = NotificationStructure(
            scenario="candidate_doc_uploaded",
            message=f"Candidate {candidate_public_id} uploaded {cat_code} - {sub_code}",
            data={
                "candidate_id": candidate_public_id,
                "category_code": cat_code,
                "sub_type_code": sub_code,
                "document_id": doc.id,
                "version_id": ver.id,
                "version_no": ver.version_no,
                "event": "uploaded",
            },
        )
        notif_payload = notif.model_dump() if hasattr(notif, "model_dump") else notif.dict()
        background_tasks.add_task(_notify_hr_application_sync, hr_employee_id, notif_payload)

    return UploadResultOut(
        document_id=doc.id,
        version_id=ver.id,
        version_no=ver.version_no,
        status=ver.status,
    )


# ============================== Core re-upload helper ==============================
async def _handle_reupload_core(
        db: Session,
        candidate_public_id: str,
        category_code: str,
        sub_type_code: str,
        file: UploadFile,
        background_tasks: Optional[BackgroundTasks] = None,
) -> UploadResultOut:
    """Internal helper for re-uploads."""
    cat_code = category_code.strip().upper()
    sub_code = _canonicalize_subtype(sub_type_code)

    if STRICT_SUBTYPE_VALIDATION:
        allowed = set(_effective_mapping(db).get(cat_code, []))
        if sub_code not in allowed:
            raise HTTPException(
                400,
                f"sub_type_code '{sub_code}' not allowed for {cat_code}. Allowed: {sorted(allowed)}",
            )

    doc = db.scalar(
        select(CandidateDocuments).where(
            CandidateDocuments.candidate_id == candidate_public_id,
            CandidateDocuments.category_code == cat_code,
            CandidateDocuments.sub_type_code == sub_code,
        )
    )
    if not doc:
        raise HTTPException(404, "No existing document found for re-upload")

    _ensure_can_reupload(doc, db)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Extension '{ext}' not allowed")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIMES:
        raise HTTPException(400, "MIME type not allowed")

    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        raise HTTPException(400, f"File too large: {size_mb:.2f} MB > {MAX_SIZE_MB} MB")
    await file.seek(0)

    current_max = (
            db.scalar(
                select(func.coalesce(func.max(DocumentVersion.version_no), 0)).where(
                    DocumentVersion.document_id == doc.id
                )
            )
            or 0
    )
    next_ver = current_max + 1
    if next_ver > MAX_VERSIONS:
        raise HTTPException(400, f"Max versions ({MAX_VERSIONS}) reached")

    # --------------- S3 UPLOAD FIXED ----------------
    s3_key = (
        f"{candidate_public_id}/{cat_code}_{sub_code}/v{next_ver}/{_sanitize_path_component(file.filename)}"
    )

    # Upload → returns s3://bucket/key
    s3_uri = _upload_to_s3(
        file_bytes=contents,
        key=s3_key,
        content_type=file.content_type or "application/octet-stream"
    )

    # Convert to correct HTTP/CloudFront URL
    url = generate_http_url(s3_uri)

    print("S3 URI STORED:", s3_uri)
    print("HTTP URL GENERATED:", url)

    ver = DocumentVersion(
        document_id=doc.id,
        version_no=next_ver,
        file_key=url,  # store final URL
        file_name=file.filename or f"file{ext}",
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
        status=DOC_STATUS_PENDING,
    )
    db.add(ver)

    doc.latest_version_no = next_ver
    doc.latest_status = DOC_STATUS_PENDING

    try:
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Re-upload failed")

    # ============================
    # CORRECT HR LOOKUP LOGIC
    # ============================
    from app.models.job_models import Application

    application = db.scalar(
        select(Application)
        .where(Application.candidate_id == candidate_public_id)
        .order_by(desc(Application.applied_at))
    )

    hr_employee_id = ""

    if application:
        job = db.scalar(select(Job).where(Job.job_id == application.job_id))
        if job and job.posted_by_employee_id:
            hr_employee_id = str(job.posted_by_employee_id).strip()

    # === Send notification ===
    if hr_employee_id:
        notif = NotificationStructure(
            scenario="candidate_doc_reuploaded",
            message=f"Candidate {candidate_public_id} re-uploaded {cat_code} - {sub_code}",
            data={
                "candidate_id": candidate_public_id,
                "category_code": cat_code,
                "sub_type_code": sub_code,
                "document_id": doc.id,
                "version_id": ver.id,
                "version_no": ver.version_no,
                "event": "reuploaded",
            },
        )
        notif_payload = notif.model_dump() if hasattr(notif, "model_dump") else notif.dict()
        background_tasks.add_task(_notify_hr_application_sync, hr_employee_id, notif_payload)

    return UploadResultOut(
        document_id=doc.id,
        version_id=ver.id,
        version_no=ver.version_no,
        status=ver.status,
    )


# ============================== Routes ==============================

# ------------------------------ Category-specific Uploads ------------------------------
@router.post("/docs/upload/education", response_model=UploadResultOut, summary="Upload EDUCATION doc")
async def upload_education_doc(
        sub_type_code: Annotated[str, Form(..., description="Education subtype (dynamic)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_upload_core(db, cand_id, "EDUCATION", sub_type_code, file, background_tasks)


@router.post("/docs/upload/id-proof", response_model=UploadResultOut, summary="Upload ID_PROOF doc")
async def upload_id_proof_doc(
        sub_type_code: Annotated[str, Form(..., description="ID proof subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_upload_core(db, cand_id, "ID_PROOF", sub_type_code, file, background_tasks)


@router.post("/docs/upload/experience", response_model=UploadResultOut, summary="Upload EXPERIENCE doc")
async def upload_experience_doc(
        sub_type_code: Annotated[str, Form(..., description="Experience subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_upload_core(db, cand_id, "EXPERIENCE", sub_type_code, file, background_tasks)


@router.post("/docs/upload/medical", response_model=UploadResultOut, summary="Upload MEDICAL doc")
async def upload_medical_doc(
        sub_type_code: Annotated[str, Form(..., description="Medical subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_upload_core(db, cand_id, "MEDICAL", sub_type_code, file, background_tasks)


# ------------------------------ Category-specific Re-uploads ------------------------------
@router.put("/docs/reupload/education", response_model=UploadResultOut, summary="Re-upload EDUCATION doc")
async def reupload_education_doc(
        sub_type_code: Annotated[str, Form(..., description="Education subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_reupload_core(db, cand_id, "EDUCATION", sub_type_code, file, background_tasks)


@router.put("/docs/reupload/id-proof", response_model=UploadResultOut, summary="Re-upload ID_PROOF doc")
async def reupload_id_proof_doc(
        sub_type_code: Annotated[str, Form(..., description="ID proof subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_reupload_core(db, cand_id, "ID_PROOF", sub_type_code, file, background_tasks)


@router.put("/docs/reupload/experience", response_model=UploadResultOut, summary="Re-upload EXPERIENCE doc")
async def reupload_experience_doc(
        sub_type_code: Annotated[str, Form(..., description="Experience subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_reupload_core(db, cand_id, "EXPERIENCE", sub_type_code, file, background_tasks)


@router.put("/docs/reupload/medical", response_model=UploadResultOut, summary="Re-upload MEDICAL doc")
async def reupload_medical_doc(
        sub_type_code: Annotated[str, Form(..., description="Medical subtype (dynamic from doc_catalog)")],
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Form(description="Public candidate id (optional; if omitted or 'string', your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)
    _ensure_application_shortlisted(db, cand_id)
    return await _handle_reupload_core(db, cand_id, "MEDICAL", sub_type_code, file, background_tasks)


# ------------------------------ Preview Documents (UNCOMMENTED) ------------------------------
@router.get("/docs/preview", summary="Preview all uploaded documents for candidate")
async def preview_documents(
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Query(description="Public candidate id (optional; if omitted, your token is used)")
        ] = None,
):
    """Get preview of all documents uploaded by candidate with their current status."""
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)

    # Get all documents for the candidate
    documents = db.scalars(
        select(CandidateDocuments)
        .where(CandidateDocuments.candidate_id == cand_id)
        .order_by(CandidateDocuments.category_code, CandidateDocuments.sub_type_code)
    ).all()

    # Get effective mapping for available categories
    effective_mapping = _effective_mapping(db)

    # Organize documents by category (DYNAMIC REQUIRED RULES)
    rules = _required_rules(db)

    preview_data = {}
    for category, subtypes in effective_mapping.items():
        preview_data[category] = {
            "required": category in rules,
            "documents": []
        }

        # Add uploaded documents
        for doc in documents:
            if doc.category_code == category:
                latest_version = None
                if doc.latest_version_no:
                    latest_version = db.scalar(
                        select(DocumentVersion)
                        .where(
                            DocumentVersion.document_id == doc.id,
                            DocumentVersion.version_no == doc.latest_version_no
                        )
                    )

                preview_data[category]["documents"].append({
                    "document_id": doc.id,
                    "sub_type_code": doc.sub_type_code,
                    "status": doc.latest_status or "NOT_UPLOADED",
                    "latest_version_no": doc.latest_version_no,
                    "file_name": latest_version.file_name if latest_version else None,
                    "file_size_bytes": latest_version.size_bytes if latest_version else None,
                    "uploaded_at": latest_version.created_at.isoformat() if latest_version and latest_version.created_at else None,
                    "can_reupload": doc.latest_status in [DOC_STATUS_REFERRED_BACK, DOC_STATUS_PENDING],
                })

        # Add missing required documents
        uploaded_subtypes = {doc.sub_type_code for doc in documents if doc.category_code == category}
        for subtype in subtypes:
            if subtype not in uploaded_subtypes:
                preview_data[category]["documents"].append({
                    "document_id": None,
                    "sub_type_code": subtype,
                    "status": "NOT_UPLOADED",
                    "latest_version_no": None,
                    "file_name": None,
                    "file_size_bytes": None,
                    "uploaded_at": None,
                    "can_reupload": False,
                })

    # Calculate readiness
    readiness = _compute_readiness(db, cand_id)

    return {
        "candidate_id": cand_id,
        "documents_by_category": preview_data,
        "readiness": readiness,
        "can_submit": readiness["ready"],
        "total_documents": len(documents),
    }


# ------------------------------ Submit Documents ------------------------------
@router.post("/docs/submit", summary="Submit all documents for BGV process")
async def submit_documents(
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Query(description="Public candidate id (optional; if omitted, your token is used)")
        ] = None,
        background_tasks: BackgroundTasks = None,
):
    """Submit all uploaded documents for BGV review process."""
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)

    # Check readiness
    readiness = _compute_readiness(db, cand_id)
    if not readiness["ready"]:
        raise HTTPException(
            400,
            f"Cannot submit: Missing required documents. Details: {readiness['missing_required']}"
        )

    documents = db.scalars(
        select(CandidateDocuments)
        .where(CandidateDocuments.candidate_id == cand_id)
    ).all()

    if not documents:
        raise HTTPException(400, "No documents found to submit")

    submitted_count = len(documents)

    # ======================================================
    # CORRECT HR LOOKUP THROUGH APPLICATION + JOB
    # ======================================================
    from app.models.job_models import Application, Job

    application = db.scalar(
        select(Application)
        .where(Application.candidate_id == cand_id)
        .order_by(desc(Application.applied_at))
    )

    hr_employee_id = ""

    if application:
        job = db.scalar(select(Job).where(Job.job_id == application.job_id))
        if job and job.posted_by_employee_id:
            hr_employee_id = str(job.posted_by_employee_id).strip()

    # ======================================================
    # Notify HR about submission (UPDATED)
    # ======================================================
    if hr_employee_id:
        notif = NotificationStructure(
            scenario="candidate_documents_submitted",
            message=f"Candidate {cand_id} submitted all documents for BGV review",
            meta_data={
                "routh_path": f"/admin/docs/review-queue/{cand_id}", 
                "http_method": "GET",
            },
            data={
                "candidate_id": cand_id,
                "submitted_documents_count": submitted_count,
                "total_documents_count": len(documents),
                "event": "documents_submitted",
            },
        )

        notif_payload = notif.model_dump() if hasattr(notif, "model_dump") else notif.dict()
        background_tasks.add_task(
            _notify_hr_application_sync,
            hr_employee_id,
            notif_payload
        )

    return {
        "message": "Documents submitted successfully for BGV review",
        "candidate_id": cand_id,
        "submitted_documents_count": submitted_count,
        "total_documents_count": len(documents),
        "submission_time": datetime.utcnow().isoformat(),
    }


# ------------------------------ List Approved Documents ------------------------------
@router.get("/docs/approved", summary="List all approved documents for candidate")
async def list_approved_documents(
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Query(description="Public candidate id (optional; if omitted, your token is used)")
        ] = None,
):
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)

    approved_docs = db.scalars(
        select(CandidateDocuments)
        .where(
            CandidateDocuments.candidate_id == cand_id,
            CandidateDocuments.latest_status == DOC_STATUS_APPROVED
        )
        .order_by(CandidateDocuments.category_code, CandidateDocuments.sub_type_code)
    ).all()

    documents_list = []
    for doc in approved_docs:
        latest_version = None
        if doc.latest_version_no:
            latest_version = db.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.version_no == doc.latest_version_no
                )
            )

        documents_list.append({
            "document_id": doc.id,
            "category_code": doc.category_code,
            "sub_type_code": doc.sub_type_code,
            "status": doc.latest_status,
            "latest_version_no": doc.latest_version_no,
            "file_name": latest_version.file_name if latest_version else None,
            "file_size_bytes": latest_version.size_bytes if latest_version else None,
            "approved_at": latest_version.updated_at.isoformat() if latest_version and latest_version.updated_at else None,
            "download_url": f"/candidate/docs/download/{doc.id}",
        })

    return {
        "candidate_id": cand_id,
        "approved_documents": documents_list,
        "total_approved": len(documents_list),
        "download_all_url": "/candidate/docs/download-all-approved",
    }

# ------------------------------ List Rejected Documents ------------------------------
@router.get("/docs/rejected", summary="List all rejected documents for candidate")
async def list_rejected_documents(
    db: Session = Depends(get_db),
    token_sub: str = Depends(candidate_session_required),
    candidate_id: Annotated[
        Optional[str],
        Query(description="Public candidate id (optional; if omitted, your token is used)")
    ] = None,
):
    """Get list of all rejected documents for the candidate."""

    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)

    rejected_docs = db.scalars(
        select(CandidateDocuments)
        .where(
            CandidateDocuments.candidate_id == cand_id,
            CandidateDocuments.latest_status == DOC_STATUS_REJECTED
        )
        .order_by(CandidateDocuments.category_code, CandidateDocuments.sub_type_code)
    ).all()

    documents_list = []
    for doc in rejected_docs:
        latest_version = None
        if doc.latest_version_no:
            latest_version = db.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.version_no == doc.latest_version_no
                )
            )

        documents_list.append({
            "document_id": doc.id,
            "category_code": doc.category_code,
            "sub_type_code": doc.sub_type_code,
            "status": doc.latest_status,
            "rejection_reason": doc.rejection_reason,
            "latest_version_no": doc.latest_version_no,
            "file_name": latest_version.file_name if latest_version else None,
            "file_size_bytes": latest_version.size_bytes if latest_version else None,
            "rejected_at": latest_version.updated_at.isoformat()
                if latest_version and latest_version.updated_at else None,
            "download_url": f"/candidate/docs/download/{doc.id}",
        })

    return {
        "candidate_id": cand_id,
        "rejected_documents": documents_list,
        "total_rejected": len(documents_list),
    }


# ------------------------------ List Referred-Back Documents ------------------------------
@router.get("/docs/referred-back", summary="List all referred-back documents for candidate")
async def list_referred_back_documents(
    db: Session = Depends(get_db),
    token_sub: str = Depends(candidate_session_required),
    candidate_id: Annotated[
        Optional[str],
        Query(description="Public candidate id (optional; if omitted, your token is used)")
    ] = None,
):
    """Get list of all referred-back documents for the candidate."""

    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)

    referred_docs = db.scalars(
        select(CandidateDocuments)
        .where(
            CandidateDocuments.candidate_id == cand_id,
            CandidateDocuments.latest_status == DOC_STATUS_REFERRED_BACK
        )
        .order_by(CandidateDocuments.category_code, CandidateDocuments.sub_type_code)
    ).all()

    documents_list = []
    for doc in referred_docs:
        latest_version = None
        if doc.latest_version_no:
            latest_version = db.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.version_no == doc.latest_version_no
                )
            )

        documents_list.append({
            "document_id": doc.id,
            "category_code": doc.category_code,
            "sub_type_code": doc.sub_type_code,
            "status": doc.latest_status,
            "remarks": doc.rejection_reason,   # remarks stored here
            "latest_version_no": doc.latest_version_no,
            "file_name": latest_version.file_name if latest_version else None,
            "file_size_bytes": latest_version.size_bytes if latest_version else None,
            "referred_back_at": latest_version.updated_at.isoformat()
                if latest_version and latest_version.updated_at else None,
            "download_url": f"/candidate/docs/download/{doc.id}",
            "reupload_allowed": True,
        })

    return {
        "candidate_id": cand_id,
        "referred_back_documents": documents_list,
        "total_referred_back": len(documents_list),
    }


# ------------------------------ Document Status Check ------------------------------
@router.get("/docs/status", summary="Get overall document submission status")
async def get_document_status(
        db: Session = Depends(get_db),
        token_sub: str = Depends(candidate_session_required),
        candidate_id: Annotated[
            Optional[str],
            Query(description="Public candidate id (optional; if omitted, your token is used)")
        ] = None,
):
    """Get overall status of document submission and BGV readiness."""
    cand_id = _resolve_candidate_id(token_sub, candidate_id)
    _ensure_candidate_exists(db, cand_id)

    status_counts = {}
    all_statuses = [DOC_STATUS_PENDING, DOC_STATUS_APPROVED, DOC_STATUS_SUBMITTED,
                    DOC_STATUS_REFERRED_BACK, DOC_STATUS_REJECTED]

    for status in all_statuses:
        count = db.scalar(
            select(func.count())
            .select_from(CandidateDocuments)
            .where(
                CandidateDocuments.candidate_id == cand_id,
                CandidateDocuments.latest_status == status
            )
        ) or 0
        status_counts[status] = count

    total_uploaded = sum(status_counts.values())

    readiness = _compute_readiness(db, cand_id)

    if status_counts[DOC_STATUS_REJECTED] > 0:
        bgv_status = "BLOCKED_REJECTED_DOCS"
    elif not readiness["ready"]:
        bgv_status = "INCOMPLETE_DOCS"
    elif status_counts[DOC_STATUS_SUBMITTED] > 0 or status_counts[DOC_STATUS_APPROVED] > 0:
        if status_counts[DOC_STATUS_APPROVED] == total_uploaded:
            bgv_status = "ALL_APPROVED"
        elif status_counts[DOC_STATUS_REFERRED_BACK] > 0:
            bgv_status = "SOME_REFERRED_BACK"
        else:
            bgv_status = "UNDER_REVIEW"
    else:
        bgv_status = "READY_TO_SUBMIT"

    return {
        "candidate_id": cand_id,
        "bgv_status": bgv_status,
        "total_documents_uploaded": total_uploaded,
        "status_breakdown": status_counts,
        "readiness": readiness,
        "can_submit": readiness["ready"] and status_counts[DOC_STATUS_PENDING] > 0,
        "can_download_approved": status_counts[DOC_STATUS_APPROVED] > 0,
    }
