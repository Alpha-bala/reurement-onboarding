#app/routers/offer_admin
from __future__ import annotations
import os
import glob
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.offer_tables import MgrOfferLetter
from app.utils.helper import require_hr_or_superadmin
from app.utils.guards import hr_session_required

# ===============================
# Router
# ===============================
router = APIRouter(
    prefix="/bgv/admin/offer-signed",
    tags=["Offer – Admin (Signed Versions)"],
    dependencies=[
        Depends(require_hr_or_superadmin),
        Depends(hr_session_required),
    ],
)

SIGNED_ROOT = os.path.join(".", "uploaded_files", "signed_offers")

# ===============================
# Helper functions (version removed)
# ===============================
def _assert_offer(db: Session, offer_id: int) -> MgrOfferLetter:
    offer = db.query(MgrOfferLetter).filter(MgrOfferLetter.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer


def _versions_dir(offer_id: int) -> str:
    return os.path.join(SIGNED_ROOT, f"offer_{offer_id}")


def _list_signed_files(offer_id: int) -> List[str]:
    """Return list of ALL signed files for offer_id without using versions."""
    d = _versions_dir(offer_id)
    if not os.path.isdir(d):
        return []
    return sorted(glob.glob(os.path.join(d, "*.pdf")))


def _latest_signed_file(offer_id: int) -> Optional[str]:
    """Return latest file based ONLY on offer_id."""
    files = _list_signed_files(offer_id)
    if not files:
        return None
    return files[-1]


# ===============================
# Download latest file (NO VERSION)
# ===============================
@router.get("/{offer_id}/download")
def admin_download_signed_version(
    offer_id: int,
    db: Session = Depends(get_db),
):
    """Download the signed PDF directly from signed_pdf_path field."""
    offer = _assert_offer(db, offer_id)

    if not offer.signed_pdf_path:
        raise HTTPException(status_code=404, detail="Signed PDF not uploaded yet")

    # Return a redirect-style FileResponse for S3 files
    return {"download_url": offer.signed_pdf_path}

@router.get("/list/all")
def admin_list_offers(
    db: Session = Depends(get_db),
):
    q = db.query(MgrOfferLetter)

    rows = q.order_by(MgrOfferLetter.created_at.desc()).all()

    out = []
    for r in rows:
        out.append({
            "offer_id": r.id,
            "candidate_id": r.candidate_id,
            "reference_number": getattr(r, "reference_number", None),
            "position": getattr(r, "position", getattr(r, "designation", None)),
            "offer_date": getattr(r, "offer_date", None),
            "joining_date": getattr(r, "joining_date", None),
            "pdf_available": bool(r.pdf_path),
            "signed_pdf_available": bool(r.signed_pdf_path),
            "signed_pdf_url": r.signed_pdf_path,
            "signed_uploaded_at": getattr(r, "signed_uploaded_at", None),
            "downloaded_at": getattr(r, "downloaded_at", None),
            "status": getattr(r, "status", None),
        })

    return out

