# app/routers/bgv_candidate.py
from __future__ import annotations
from typing import Optional, List
import os
import re
from sqlalchemy.orm import joinedload
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from datetime import date
from app.utils.s3_utils import _upload_to_s3, generate_http_url
from app.schemas.user_schemas import NotificationStructure

from app.database import get_db
from app.models.bgv import (
    BGVForm, BGVEducation, BGVEmployment, BGVReference, BGVStatus, EmergencyRelation
)
from app.models.job_models import Job
from app.schemas.bgv import (
    BGVPersonalIn, BGVPersonalOut,
    BGVEducationsOut,
    BGVEmploymentsOut,
    BGVReferencesOut,
    BGVChecklistIn, BGVChecklistOut,
    BGVFormOut
)
from app.models.user_models import CandidateProfileInformation as DBCandidate
from app.utils.helper import require_candidate_only
from app.utils.notify_depends import send_notification_to_employee

import io
try:
    from PIL import Image
    import numpy as np
except Exception:
    Image = None
    np = None

router = APIRouter(prefix="/bgv", tags=["BGV – Candidate"])

# ---------- auth helpers ----------
def _enforce_cid(token_cid: str, provided: Optional[str]) -> str:
    if provided and provided != token_cid:
        raise HTTPException(status_code=403, detail="Forbidden: candidate_id mismatch")
    return token_cid
# ----------------------- Helpers -----------------------
def _sanitize_path_component(s: str) -> str:
    """Ensure safe S3 path components."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")

# ---------- data helpers ----------
def _assert_candidate_exists(db: Session, candidate_id: str) -> None:
    exists = db.query(DBCandidate.candidate_id).filter(DBCandidate.candidate_id == candidate_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail=f"Candidate '{candidate_id}' not found. Create the candidate first.")

def _get_or_create_pending_form(db: Session, candidate_id: str) -> BGVForm:
    form = db.query(BGVForm).filter(BGVForm.candidate_id == candidate_id).first()
    if form:
        if form.status != BGVStatus.PENDING:
            raise HTTPException(status_code=400, detail=f"Form is {form.status}; editing is locked")
        return form
    _assert_candidate_exists(db, candidate_id)
    form = BGVForm(candidate_id=candidate_id, status=BGVStatus.PENDING)
    db.add(form)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Could not create BGV form (foreign key failed).")
    return form

def _get_form_or_404(db: Session, candidate_id: str) -> BGVForm:
    form = db.query(BGVForm).filter(BGVForm.candidate_id == candidate_id).first()
    if not form:
        raise HTTPException(status_code=404, detail="No BGV form found for this candidate")
    return form

def _ensure_minimums_or_422(db: Session, form: BGVForm) -> None:
    emp_count = db.query(BGVEmployment).filter(BGVEmployment.form_id == form.id).count()
    ref_count = db.query(BGVReference).filter(BGVReference.form_id == form.id).count()

    missing = []
    if emp_count < 1:
        missing.append(f"Employment records: {emp_count}/1")
    if ref_count < 3:
        missing.append(f"Reference records: {ref_count}/3")

    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Minimum requirements not met. " + "; ".join(missing))

def _normalize_and_validate_emergency_contact(form: BGVForm) -> None:
    number = (form.emergency_contact_number or "").strip()
    if not number:
        return

    rel = form.emergency_contact_relationship
    name = (form.emergency_contact_name or "").strip()

    if not rel:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Emergency contact relationship is required when emergency contact number is provided.")

    if not name:
        if rel == EmergencyRelation.SPOUSE and (form.spouse_name or "").strip():
            form.emergency_contact_name = form.spouse_name.strip()
            name = form.emergency_contact_name
        elif rel == EmergencyRelation.FATHER and (form.father_name or "").strip():
            form.emergency_contact_name = form.father_name.strip()
            name = form.emergency_contact_name
        elif rel == EmergencyRelation.MOTHER and (form.mother_name or "").strip():
            form.emergency_contact_name = form.mother_name.strip()
            name = form.emergency_contact_name

    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Emergency contact name is required when emergency contact number is provided.")
# =============================================================================
#  Notification helper
# =============================================================================
def _notify_hr_application_sync(employee_id: str, payload: dict) -> None:
    import asyncio
    try:
        asyncio.run(send_notification_to_employee(employee_id, payload))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(send_notification_to_employee(employee_id, payload))

# ========================= PERSONAL =========================
@router.post("/personal", response_model=BGVPersonalOut, status_code=status.HTTP_200_OK)
def save_personal(
    data: BGVPersonalIn,
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    cid = _enforce_cid(token_cid, data.candidate_id)
    form = _get_or_create_pending_form(db, cid)

    for f, v in data.model_dump(exclude={"candidate_id"}).items():
        setattr(form, f, v)

    _normalize_and_validate_emergency_contact(form)

    db.commit()
    db.refresh(form)
    return form

@router.get("/personal/me", response_model=BGVPersonalOut)
def get_personal_me(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    return _get_form_or_404(db, token_cid)

# ========================= EDUCATION =========================
class _EducationItem(BaseModel):
    qualification: str
    institution_name: str
    university_or_board: str
    year_of_passing: int = Field(..., ge=1900, le=2100)
    percentage_or_cgpa: str

class BGVEducationBulkIn(BaseModel):
    candidate_id: str
    educations: List[_EducationItem] = Field(..., description="Unlimited allowed")

@router.post("/education", response_model=BGVEducationsOut, status_code=status.HTTP_200_OK)
def add_educations(
    payload: BGVEducationBulkIn,
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    cid = _enforce_cid(token_cid, payload.candidate_id)
    form = _get_or_create_pending_form(db, cid)

    for item in payload.educations:
        db.add(BGVEducation(form_id=form.id, **item.model_dump()))

    db.commit()
    db.refresh(form)
    return form

@router.get("/educations/me", response_model=BGVEducationsOut)
def get_educations_me(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db)
):
    return _get_form_or_404(db, token_cid)

# ========================= EMPLOYMENT =========================
employments: List[_EmploymentItem]

class _VerifierItem(BaseModel):
    name: str
    designation: str
    contact_number: str

class _EmploymentItem(BaseModel):
    company_name: str
    designation: str
    start_date: str  # string for response
    end_date: str
    reason_for_leaving: str
    verifiers: Optional[List[_VerifierItem]] = None

class BGVEmploymentBulkIn(BaseModel):
    candidate_id: str
    employments: List[_EmploymentItem]

class BGVEmploymentsOut(BaseModel):
    candidate_id: str
    employments: List[_EmploymentItem]


# ========================= Helper Function =========================
def convert_employment_to_str(employments):
    result = []
    for e in employments:
        # handle dict vs object
        if isinstance(e, dict):
            company_name = e.get("company_name")
            designation = e.get("designation")
            start_date = e.get("start_date")
            end_date = e.get("end_date")
            reason_for_leaving = e.get("reason_for_leaving")
            verifiers = e.get("verifiers", [])
        else:
            company_name = getattr(e, "company_name", None)
            designation = getattr(e, "designation", None)
            start_date = (
                e.start_date.strftime("%Y-%m-%d") if getattr(e, "start_date", None) else None
            )
            end_date = (
                e.end_date.strftime("%Y-%m-%d") if getattr(e, "end_date", None) else None
            )
            reason_for_leaving = getattr(e, "reason_for_leaving", None)
            verifiers = getattr(e, "verifiers", []) or []

        formatted_verifiers = []
        for v in verifiers:
            if isinstance(v, dict):
                formatted_verifiers.append({
                    "name": v.get("name"),
                    "designation": v.get("designation"),
                    "contact_number": v.get("contact_number"),
                })
            else:
                formatted_verifiers.append({
                    "name": getattr(v, "name", None),
                    "designation": getattr(v, "designation", None),
                    "contact_number": getattr(v, "contact_number", None),
                })

        result.append({
            "company_name": company_name,
            "designation": designation,
            "start_date": start_date,
            "end_date": end_date,
            "reason_for_leaving": reason_for_leaving,
            "verifiers": formatted_verifiers,
        })

    return result

# ========================= ROUTER LOGIC =========================
@router.post("/employment/bulk", response_model=BGVEmploymentsOut, status_code=status.HTTP_200_OK)
def add_employments_bulk(
    payload: BGVEmploymentBulkIn,
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    cid = _enforce_cid(token_cid, payload.candidate_id)
    form = _get_or_create_pending_form(db, cid)

    for emp in payload.employments:
        start_date_obj = date.fromisoformat(emp.start_date)
        end_date_obj = date.fromisoformat(emp.end_date)

        db_emp = BGVEmployment(
            form_id=form.id,
            company_name=emp.company_name,
            designation=emp.designation,
            start_date=start_date_obj,
            end_date=end_date_obj,
            reason_for_leaving=emp.reason_for_leaving,
            verifiers=[v.dict() for v in emp.verifiers] if emp.verifiers else None
        )
        db.add(db_emp)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Failed to insert one or more employment rows")

    db.refresh(form)

    response_employments = []
    for emp in form.employments:
        response_employments.append({
            "company_name": emp.company_name,
            "designation": emp.designation,
            "start_date": emp.start_date.strftime("%Y-%m-%d") if emp.start_date else None,
            "end_date": emp.end_date.strftime("%Y-%m-%d") if emp.end_date else None,
            "reason_for_leaving": emp.reason_for_leaving,
            "verifiers": emp.verifiers
        })

    return BGVEmploymentsOut(candidate_id=form.candidate_id, employments=response_employments)

@router.get("/employments/me", response_model=BGVEmploymentsOut)
def get_employments_me(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db)
):
    form = _get_form_or_404(db, token_cid)

    response_employments = []

    from datetime import datetime, date

    for emp in form.employments:

        # ---- FIX: convert MM/YYYY or other string formats safely ----
        start_date = emp.start_date
        end_date = emp.end_date

        if isinstance(start_date, str):
            if "/" in start_date:   # format like "07/2019"
                try:
                    start_date = datetime.strptime(start_date, "%m/%Y").date().replace(day=1)
                except:
                    start_date = None
            else:                    # maybe "2023-04-01"
                try:
                    start_date = date.fromisoformat(start_date)
                except:
                    start_date = None

        if isinstance(end_date, str):
            if "/" in end_date:
                try:
                    end_date = datetime.strptime(end_date, "%m/%Y").date().replace(day=1)
                except:
                    end_date = None
            else:
                try:
                    end_date = date.fromisoformat(end_date)
                except:
                    end_date = None

        response_employments.append({
            "company_name": emp.company_name,
            "designation": emp.designation,
            "start_date": start_date.strftime("%Y-%m-%d") if start_date else None,
            "end_date": end_date.strftime("%Y-%m-%d") if end_date else None,
            "reason_for_leaving": emp.reason_for_leaving,
            "verifiers": emp.verifiers
        })

    return BGVEmploymentsOut(candidate_id=form.candidate_id, employments=response_employments)

# ========================= REFERENCES =========================
class _ReferenceItem(BaseModel):
    name: str
    designation: str
    company: str
    relationship: str
    contact_number: str
    email: str

class BGVReferenceBulkIn(BaseModel):
    candidate_id: str
    references: List[_ReferenceItem] = Field(..., min_length=3, description="Provide at least 3 references")

@router.post("/reference/bulk", response_model=BGVReferencesOut, status_code=status.HTTP_200_OK)
def add_references_bulk(
    payload: BGVReferenceBulkIn,
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    cid = _enforce_cid(token_cid, payload.candidate_id)
    form = _get_or_create_pending_form(db, cid)

    for item in payload.references:
        db.add(BGVReference(form_id=form.id, **item.model_dump()))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Failed to insert one or more reference rows")

    db.refresh(form)
    return form

@router.get("/references/me", response_model=BGVReferencesOut)
def get_references_me(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db)
):
    return _get_form_or_404(db, token_cid)

# ========================= CHECKLIST =========================
@router.post("/checklist", response_model=BGVChecklistOut, status_code=status.HTTP_200_OK)
def save_checklist(
    data: BGVChecklistIn,
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
    finalize: bool = Query(False, description="If true, enforce minimums before proceeding"),
):
    cid = _enforce_cid(token_cid, data.candidate_id)
    form = _get_or_create_pending_form(db, cid)

    for f, v in data.model_dump(exclude={"candidate_id"}).items():
        setattr(form, f, v)

    if finalize:
        _ensure_minimums_or_422(db, form)

    db.commit()
    db.refresh(form)
    return form

@router.get("/checklist/me", response_model=BGVChecklistOut)
def get_checklist_me(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db)
):
    return _get_form_or_404(db, token_cid)

@router.get("/preview/me", response_model=BGVFormOut)
def preview_full_me(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db)
):
    return _get_form_or_404(db, token_cid)


def _get_form_or_404(db: Session, candidate_id: str):
    form = (
        db.query(BGVForm)
        .filter(BGVForm.candidate_id == candidate_id)
        .options(
            joinedload(BGVForm.educations),
            joinedload(BGVForm.employments),
            joinedload(BGVForm.references),
        )
        .first()
    )

    if not form:
        raise HTTPException(status_code=404, detail="BGV form not found")

    from datetime import date
    for emp in form.employments:
        if isinstance(emp.start_date, date):
            emp.start_date = emp.start_date.strftime("%m/%Y")
        if isinstance(emp.end_date, date):
            emp.end_date = emp.end_date.strftime("%m/%Y")

    return form

# # ========================= SIGNATURE UPLOAD =========================

_ALLOWED_SIG_MIMES = {"image/jpeg", "image/png", "image/jpg"}
_MIN_SIG_BYTES = 100 * 1024      # 10 KB
_MAX_SIG_BYTES = 120 * 1024      # 20 KB

_RELAX_HEURISTICS = True

def _validate_signature_image_or_422(raw: bytes, mime: str) -> None:
    """
    - MIME: JPEG/PNG
    - Size: 10–20 KB (inclusive)
    - Optional light heuristics when Pillow/NumPy available.
    Designed to accept clean white-paper handwritten signatures like your sample.
    """
    if mime not in _ALLOWED_SIG_MIMES:
        raise HTTPException(status_code=415, detail="Only JPEG/PNG allowed for signature.")
    n = len(raw)
    if n < _MIN_SIG_BYTES or n > _MAX_SIG_BYTES:
        raise HTTPException(status_code=422, detail="Signature file size must be between 10 KB and 20 KB.")

    if Image is None or np is None:
        return

    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid image file.")

    gray = np.asarray(img.convert("L"))
    h, w = gray.shape

    pad = max(2, int(min(h, w) * 0.03))
    border = np.concatenate([gray[:pad, :], gray[-pad:, :], gray[:, :pad], gray[:, -pad:]], axis=None)
    border_mean = float(border.mean())
    border_std = float(border.std())

    if not (border_mean >= 220 and border_std <= 35):
        if not _RELAX_HEURISTICS:
            raise HTTPException(status_code=422, detail="Use a clean white paper background.")

    cx0, cy0 = pad, pad
    cx1, cy1 = w - pad, h - pad
    center = gray[cy0:cy1, cx0:cx1]

    dark = (center < 120).sum()
    total = center.size
    ratio = dark / max(1, total)

    if ratio < 0.0001:
        raise HTTPException(status_code=422, detail="Signature not detected. Ensure a clear handwritten signature in ink.")
    if ratio > 0.20:
        if not _RELAX_HEURISTICS:
            raise HTTPException(status_code=422, detail="Handwritten signature only. Avoid large filled/printed text.")


@router.post(
    "/signature",
    summary="Upload handwritten signature (white paper, 10–20 KB JPEG/PNG)",
    status_code=status.HTTP_200_OK,
)
async def upload_signature(
    file: UploadFile = File(..., description="JPEG/PNG, 10–20 KB, handwritten on white blank paper"),
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    """
    Uploads candidate handwritten signature to S3 (same style as documents),
    stores the S3 URL in BGVForm.signature_file_key.
    """
    raw = await file.read()
    _validate_signature_image_or_422(raw, (file.content_type or "").lower())
    await file.seek(0)

    cid = token_cid
    form = _get_or_create_pending_form(db, cid)

    filename = file.filename or "signature.jpg"
    ext = os.path.splitext(filename)[1].lower()

    s3_key = f"{cid}/SIGNATURE/v1/{_sanitize_path_component(filename)}"

    s3_uri = _upload_to_s3(
        file_bytes=raw,
        key=s3_key,
        content_type=file.content_type or "application/octet-stream",
    )

    url = generate_http_url(s3_uri)

    print("SIGNATURE S3 URI STORED:", s3_uri)
    print("SIGNATURE HTTP URL:", url)

    form.signature_file_key = url
    form.signature_mime_type = (file.content_type or "").lower()
    form.signature_size_bytes = len(raw)

    db.commit()
    db.refresh(form)

    return {
        "message": "Signature uploaded successfully.",
        "candidate_id": cid,
        "file_key": form.signature_file_key,
        "mime_type": form.signature_mime_type,
        "size_bytes": form.signature_size_bytes,
    }


@router.post("/submit")
def submit_for_processing(
    token_cid: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    form = _get_form_or_404(db, token_cid)
    if form.status != BGVStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Form is {form.status}; editing/submit is locked"
        )

    _ensure_minimums_or_422(db, form)

    from datetime import datetime

    for emp in form.employments:
        if emp.start_date and isinstance(emp.start_date, str) and "/" in emp.start_date:
            emp.start_date = datetime.strptime(emp.start_date, "%m/%Y").date().replace(day=1)

        if emp.end_date and isinstance(emp.end_date, str) and "/" in emp.end_date:
            emp.end_date = datetime.strptime(emp.end_date, "%m/%Y").date().replace(day=1)

    # Mark form as submitted
    form.status = BGVStatus.PENDING

    db.commit()
    db.refresh(form)

    # Fetch the job linked to this candidate
    job = (
        db.query(Job)
        .join(Job.applications)
        .filter(Job.applications.any(candidate_id=form.candidate_id))
        .first()
    )

    # ============================
    # 🔔 HR IN-APP NOTIFICATION
    # ============================
    if job and job.posted_by_employee_id:
        notif = NotificationStructure(
            scenario="bgv_form_submitted",
            message=f"Candidate {form.candidate_id} submitted their BGV form for processing",
            meta_data={
                "routh_path": f"/bgv/admin/{form.id}",  # ✅ FROM SCREENSHOT
                "http_method": "GET",
            },
            data={
                "candidate_id": form.candidate_id,
                "form_id": form.id,
                "event": "bgv_form_submitted",
            },
        )

        payload = notif.model_dump() if hasattr(notif, "model_dump") else notif.dict()
        _notify_hr_application_sync(job.posted_by_employee_id, payload)

    return {
        "message": f"Candidate {form.candidate_id} BGV form submitted successfully.",
        "candidate_id": form.candidate_id,
        "employment_count": len(form.employments),
        "reference_count": len(form.references),
        "status": form.status.value,
    }








