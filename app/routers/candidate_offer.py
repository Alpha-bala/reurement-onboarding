from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional, List

import requests
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    BackgroundTasks,
    Query,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.offer_tables import MgrOfferLetter
from app.models.job_models import Job
from app.schemas.user_schemas import NotificationStructure
from app.utils.notify_depends import send_notification_to_employee
from app.utils.helper import require_candidate_only
from app.utils.guards import candidate_session_required
from app.utils.s3_utils import _upload_to_s3, generate_http_url


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/candidate/offer",
    tags=["Candidate – Offer Letter"],
    dependencies=[Depends(candidate_session_required)],
)

# =============================================================================
# Helpers
# =============================================================================
def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".", " ") else "_" for c in (s or ""))


def _assert_offer(db: Session, offer_id: int) -> MgrOfferLetter:
    offer = db.query(MgrOfferLetter).filter(MgrOfferLetter.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer


def _list_versions(offer: MgrOfferLetter) -> List[dict]:
    if not getattr(offer, "signed_versions", None):
        return []
    return sorted(offer.signed_versions, key=lambda x: x.get("version", 0))


def _latest_version_s3_url(offer: MgrOfferLetter) -> Optional[str]:
    versions = _list_versions(offer)
    return versions[-1]["s3_url"] if versions else None


def _notify_hr_application_sync(employee_id: str, payload: dict) -> None:
    if not employee_id:
        return
    try:
        asyncio.run(send_notification_to_employee(employee_id, payload))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(send_notification_to_employee(employee_id, payload))


# =============================================================================
# LIST MY OFFERS (EXISTING — UNCHANGED)
# =============================================================================
@router.get("/list", summary="List all offers for logged-in candidate")
def list_my_offers(
    db: Session = Depends(get_db),
    candidate_id: str = Depends(require_candidate_only),
):
    rows = (
        db.query(MgrOfferLetter)
        .filter(MgrOfferLetter.candidate_id == candidate_id)
        .order_by(MgrOfferLetter.id.desc())
        .all()
    )

    output = []
    for r in rows:
        versions = _list_versions(r)
        output.append({
            "id": r.id,
            "offer_id": r.id,
            "candidate_id": r.candidate_id,
            "reference_number": r.reference_number,
            "position": r.position,
            "designation": r.designation,
            "offer_date": r.offer_date,
            "joining_date": r.joining_date,
            "acceptance_deadline": r.acceptance_deadline,
            "annual_ctc": r.annual_ctc,
            "variable_pay": r.variable_pay,
            "band": r.band,
            "regime": r.regime,
            "notice_period": r.notice_period,
            "probation_period": r.probation_period,
            "office_timings": r.office_timings,
            "office_name": r.office_name,
            "office_location": r.office_location,
            "reporting_person": r.reporting_person,
            "reporting_contact_number": r.reporting_contact_number,
            "reporting_time": r.reporting_time,
            "reporting_office_address": r.reporting_office_address,
            "co_founder_and_director": r.co_founder_and_director,
            "pdf_path": r.pdf_path,
            "signed_pdf_path": r.signed_pdf_path or _latest_version_s3_url(r),
            "signed_versions": versions,
            "candidate_status": r.candidate_status or "PENDING",
            "signed_uploaded_at": r.signed_uploaded_at,
            "viewed_at": r.viewed_at,
            "downloaded_at": r.downloaded_at,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        })
    return output


# =============================================================================
# LIST MY OFFERS — PAGINATED (NEW, OPTIONAL)
# =============================================================================
@router.get("/page")
def list_my_offers_page(
    db: Session = Depends(get_db),
    candidate_id: str = Depends(require_candidate_only),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    q = db.query(MgrOfferLetter).filter(MgrOfferLetter.candidate_id == candidate_id)
    total = q.count()

    rows = (
        q.order_by(MgrOfferLetter.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "items": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# =============================================================================
# DOWNLOAD OFFER (UNCHANGED PATH)
# =============================================================================
@router.get("/download/{offer_id}")
def download_offer(
    offer_id: int,
    candidate_id: str = Depends(require_candidate_only),
    db: Session = Depends(get_db),
):
    offer = _assert_offer(db, offer_id)

    if offer.candidate_id != candidate_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if not offer.pdf_path:
        raise HTTPException(status_code=404, detail="Offer PDF not available")

    now = datetime.utcnow()
    if not offer.viewed_at:
        offer.viewed_at = now
    offer.downloaded_at = now

    db.commit()

    response = requests.get(offer.pdf_path, stream=True)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch PDF")

    filename = f"OfferLetter_{offer.reference_number}.pdf"
    return StreamingResponse(
        response.raw,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# =============================================================================
# UPLOAD SIGNED OFFER
# =============================================================================
@router.post("/{offer_id}/upload-signed")
async def upload_signed_offer(
    offer_id: int,
    background_tasks: BackgroundTasks,
    candidate_id: str = Depends(require_candidate_only),
    file: UploadFile = File(..., description="Signed offer PDF"),
    db: Session = Depends(get_db),
):
    offer = _assert_offer(db, offer_id)

    if offer.candidate_id != candidate_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid file")

    if (file.content_type or "").lower() not in (
        "application/pdf",
        "application/octet-stream",
    ):
        raise HTTPException(status_code=415, detail="Only PDF allowed")

    versions = _list_versions(offer)
    next_version = (versions[-1]["version"] + 1) if versions else 1

    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    fname = f"v{next_version}_{_safe_name(candidate_id)}_{ts}.pdf"

    file_bytes = await file.read()
    s3_key = f"signed_offers/offer_{offer_id}/{fname}"
    s3_url = generate_http_url(_upload_to_s3(file_bytes, s3_key, file.content_type))

    offer.signed_versions = offer.signed_versions or []
    offer.signed_versions.append({
        "version": next_version,
        "s3_url": s3_url,
        "filename": fname,
        "uploaded_at": datetime.utcnow(),
    })

    offer.signed_pdf_path = s3_url
    offer.signed_uploaded_at = datetime.utcnow()
    offer.candidate_status = "ACCEPTED"

    db.commit()
    db.refresh(offer)

    job = (
        db.query(Job)
        .join(Job.applications)
        .filter(Job.applications.any(candidate_id=candidate_id))
        .first()
    )

    # ============================
    # 🔔 HR IN-APP NOTIFICATION
    # ============================
    if job and job.posted_by_employee_id:
        notif = NotificationStructure(
            scenario="candidate_signed_offer_uploaded",
            message=f"Candidate {candidate_id} uploaded signed offer (v{next_version})",
            meta_data={
                "routh_path": f"/bgv/admin/offer-signed/{offer.id}/download",  # ✅ FROM SCREENSHOT
                "http_method": "GET",
            },
            data={
                "candidate_id": candidate_id,
                "offer_id": offer.id,
                "version": next_version,
                "s3_url": s3_url,
            },
        )

        background_tasks.add_task(
            _notify_hr_application_sync,
            job.posted_by_employee_id,
            notif.model_dump() if hasattr(notif, "model_dump") else notif.dict(),
        )

    return {
        "message": "Signed offer uploaded successfully",
        "offer_id": offer.id,
        "candidate_id": offer.candidate_id,
        "version": next_version,
        "signed_pdf_path": offer.signed_pdf_path,
        "signed_uploaded_at": offer.signed_uploaded_at,
        "candidate_status": offer.candidate_status,
    }

