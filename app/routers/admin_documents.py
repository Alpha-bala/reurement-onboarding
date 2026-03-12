#app/routers/admin_documents
import os
from typing import Optional
from datetime import datetime
from app.database import SessionLocal
import logging
import asyncio
import tempfile
import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.documents import CandidateDocuments, DocumentVersion
from app.models.user_models import CandidateProfileInformation as DBCandidate
from app.utils.aws_email import aws_send_mail
from app.schemas.user_schemas import NotificationStructure
from app.utils.notify_depends import send_notification_to_candidate as _notify_candidate_async
from app.utils.helper import require_hr_or_superadmin
from app.utils.guards import hr_session_required

# =======================================================================
# Router Definition (Centralized Guards)
# =======================================================================
router = APIRouter(
    prefix="/admin/docs",
    tags=["Admin/HR – Documents"],
    dependencies=[
        Depends(require_hr_or_superadmin),  # Role-based guard (HR or SuperAdmin)
        Depends(hr_session_required),       # Active session validation
    ],
)

logger = logging.getLogger(__name__)

# ---------------------------
# Config
# ---------------------------
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_REFERRED_BACK = "REFERRED_BACK"
STATUS_PENDING = "PENDING"

# =======================================================================
# S3 Helpers
# =======================================================================
def download_s3_intere(file_key: str) -> str:
    bucket_name = os.getenv("AWS_S3_BUCKET")   # ← FIXED

    if not bucket_name:
        raise ValueError("AWS_S3_BUCKET environment variable is missing")

    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )

    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()

    try:
        s3.download_file(bucket_name, file_key, temp_file.name)
        return temp_file.name
    except ClientError:
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
        raise HTTPException(status_code=404, detail=f"File not found in S3: {file_key}")

# ==========================================================
# HELPER: Build human-readable name
# ==========================================================
def _human_name(first: Optional[str], last: Optional[str], fallback: str) -> str:
    """
    Build a clean display name from first + last name.
    Falls back safely if both are missing.
    """
    parts = [p for p in [(first or "").strip(), (last or "").strip()] if p]
    return " ".join(parts) if parts else fallback


# ==========================================================
# HELPER: Send plain text email (SES)
# ==========================================================
def _send_mail(to_email: Optional[str], subject: str, body: str) -> bool:
    """
    Send plain text email using Amazon SES (SMTP).
    Wrapper around aws_send_mail().
    """

    if not to_email:
        logger.warning("[EMAIL] No recipient email provided")
        return False

    try:
        # aws_send_mail is async → run safely from sync context
        asyncio.run(
            aws_send_mail(
                reciver_to=[to_email],
                subject=subject,
                body=body,
                body_type="plain",
            )
        )

        logger.info(f"[EMAIL] Sent to {to_email}")
        return True

    except RuntimeError:
        # If already inside an event loop (FastAPI background task etc.)
        loop = asyncio.get_event_loop()
        loop.create_task(
            aws_send_mail(
                reciver_to=[to_email],
                subject=subject,
                body=body,
                body_type="plain",
            )
        )
        logger.info(f"[EMAIL] Scheduled async send to {to_email}")
        return True

    except Exception as e:
        logger.error(f"[EMAIL ERROR] to={to_email} subject={subject} error={e}")
        return False

async def _send_rejection_email(
    to_email: str,
    candidate_name: str,
    candidate_id: str,
    category: str,
    sub_type: str,
    reason: str,
):
    await aws_send_mail(
        reciver_to=[to_email],
        subject=f"[Final Decision] Document Rejected – {category}/{sub_type}",
        body=(
            f"Dear {candidate_name},\n\n"
            f"Your uploaded document has been rejected.\n\n"
            f"Candidate ID: {candidate_id}\n"
            f"Document: {category} / {sub_type}\n"
            f"Status: {STATUS_REJECTED}\n"
            f"Reason: {reason}\n\n"
            "You will not be able to re-upload this document again.\n\n"
            "Regards,\nHR Team"
        ),
        body_type="plain",
    )
# =======================================================================
# NOTIFICATION HELPERS
# =======================================================================

def _notify_candidate_sync(candidate_id: str, notif: NotificationStructure) -> None:
    """
    Safe synchronous wrapper for candidate notifications
    (NO schema / notify_depends changes)
    """
    if not candidate_id or not notif:
        return

    db = SessionLocal()
    try:
        asyncio.run(
            _notify_candidate_async(
                candidate_id=candidate_id,
                notification_data=notif,  # ✅ pass NotificationStructure
                db=db                      # ✅ pass DB session
            )
        )
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(
            _notify_candidate_async(candidate_id, notif, db)
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[Candidate Notification Failed] {candidate_id}: {e}")
    finally:
        db.close()



def _notif_document_status(
    doc: CandidateDocuments,
    status: str,
    reason: Optional[str] = None
):
    route_map = {
        STATUS_APPROVED: "/candidate/docs/approved",
        STATUS_REJECTED: "/candidate/docs/rejected",
        STATUS_REFERRED_BACK: "/candidate/docs/referred-back",
    }

    scenario = f"document_{status.lower()}"
    message = f"Document {doc.category_code}/{doc.sub_type_code} {status.replace('_', ' ').title()}"

    data = {
        "candidate_id": doc.candidate_id,
        "category_code": doc.category_code,
        "sub_type_code": doc.sub_type_code,
        "status": status,
        "updated_at_utc": datetime.utcnow().isoformat(),
    }

    if reason:
        data["reason"] = reason

    return NotificationStructure(
        scenario=scenario,
        message=message,
        meta_data={
            "routh_path": route_map.get(status),  # ✅ redirect path
            "http_method": "GET",
        },
        data=data,
    )


# =======================================================================
# ADMIN – REVIEW QUEUE
# =======================================================================
@router.get("/review-queue")
def review_queue_all(db: Session = Depends(get_db)):
    """Return all candidate documents (admin view)."""
    docs = db.query(CandidateDocuments).order_by(CandidateDocuments.updated_at.desc()).all()
    items = []
    for doc in docs:
        latest_ver = None
        if doc.latest_version_no:
            latest_ver = db.query(DocumentVersion).filter_by(
                document_id=doc.id,
                version_no=doc.latest_version_no
            ).first()

        items.append({
            "candidate_id": doc.candidate_id,
            "category_code": doc.category_code,
            "sub_type_code": doc.sub_type_code,
            "status": doc.latest_status,
            "latest_version_no": doc.latest_version_no,
            "file_name": latest_ver.file_name if latest_ver else None,
            "mime_type": latest_ver.mime_type if latest_ver else None,
            "size_bytes": latest_ver.size_bytes if latest_ver else None,
            "uploaded_at": latest_ver.created_at if latest_ver else None,
            "rejection_reason": getattr(doc, "rejection_reason", None),
        })

    return {"count": len(items), "items": items}


@router.get("/review-queue/{candidate_id}")
def review_queue_for_candidate(candidate_id: str, db: Session = Depends(get_db)):
    docs = db.query(CandidateDocuments).filter(CandidateDocuments.candidate_id == candidate_id).all()
    items = []

    for doc in docs:
        latest_ver = None
        if doc.latest_version_no:
            latest_ver = db.query(DocumentVersion).filter_by(
                document_id=doc.id,
                version_no=doc.latest_version_no
            ).first()

        items.append({
            "candidate_id": doc.candidate_id,
            "category_code": doc.category_code,
            "sub_type_code": doc.sub_type_code,
            "status": doc.latest_status,
            "latest_version_no": doc.latest_version_no,
            "file_name": latest_ver.file_name if latest_ver else None,
            "mime_type": latest_ver.mime_type if latest_ver else None,
            "size_bytes": latest_ver.size_bytes if latest_ver else None,
            "uploaded_at": latest_ver.created_at if latest_ver else None,
            "rejection_reason": getattr(doc, "rejection_reason", None),
        })

    return {"candidate_id": candidate_id, "count": len(items), "items": items}


# =======================================================================
# DOCUMENT DOWNLOAD / PREVIEW (S3)
# =======================================================================
@router.get("/{candidate_id}/{category}/{sub_type}/preview")
def preview_doc_s3(candidate_id: str, category: str, sub_type: str, db: Session = Depends(get_db)):
    """Preview a document from S3."""
    doc = db.query(CandidateDocuments).filter_by(
        candidate_id=candidate_id, category_code=category, sub_type_code=sub_type
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    latest_ver = None
    if doc.latest_version_no:
        latest_ver = db.query(DocumentVersion).filter_by(
            document_id=doc.id,
            version_no=doc.latest_version_no
        ).first()

    if not latest_ver or not latest_ver.file_key:
        raise HTTPException(status_code=404, detail="Document file not found")

    raw_key = latest_ver.file_key

    # ---------------------------------------
    # 1. Strip protocol + bucket domain
    # ---------------------------------------
    if raw_key.startswith("http://") or raw_key.startswith("https://"):
        raw_key = raw_key.split(".amazonaws.com/")[-1]

    # ---------------------------------------
    # 2. Remove ?AWSAccessKeyId=... etc
    # ---------------------------------------
    if "?" in raw_key:
        raw_key = raw_key.split("?")[0]

    # final S3 key
    s3_key = raw_key

    temp_file_path = download_s3_intere(s3_key)

    return FileResponse(
        path=temp_file_path,
        media_type=latest_ver.mime_type,
        filename=latest_ver.file_name
    )

@router.get("/{candidate_id}/{category}/{sub_type}/download")
def download_doc_s3(candidate_id: str, category: str, sub_type: str, db: Session = Depends(get_db)):
    doc = db.query(CandidateDocuments).filter_by(
        candidate_id=candidate_id, category_code=category, sub_type_code=sub_type
    ).first()
    if not doc:
        raise HTTPException(404, "Document not found")

    latest_ver = None
    if doc.latest_version_no:
        latest_ver = db.query(DocumentVersion).filter_by(
            document_id=doc.id,
            version_no=doc.latest_version_no
        ).first()

    if not latest_ver or not latest_ver.file_key:
        raise HTTPException(404, "Document file not found")

    raw_key = latest_ver.file_key

    # Remove protocol + bucket domain if present
    if raw_key.startswith("http://") or raw_key.startswith("https://"):
        # convert full URL → key
        raw_key = raw_key.split(".amazonaws.com/")[-1]

    # Remove presigned URL query parameters
    if "?" in raw_key:
        raw_key = raw_key.split("?")[0]

    s3_key = raw_key
    temp_file_path = download_s3_intere(s3_key)

    headers = {"Content-Disposition": f'attachment; filename=\"{latest_ver.file_name}\"'}

    return FileResponse(
        path=temp_file_path,
        media_type=latest_ver.mime_type,
        filename=latest_ver.file_name,
        headers=headers
    )

@router.put("/{candidate_id}/{category}/{sub_type}/approve")
def approve_doc(candidate_id: str, category: str, sub_type: str, db: Session = Depends(get_db)):
    """Approve a candidate document."""
    doc = db.query(CandidateDocuments).filter_by(
        candidate_id=candidate_id, category_code=category, sub_type_code=sub_type
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Update candidate_documents
    doc.latest_status = STATUS_APPROVED
    doc.rejection_reason = None

    # 🔥 Update document_versions EXACTLY same as latest_status
    if doc.latest_version_no:
        latest_ver = db.query(DocumentVersion).filter_by(
            document_id=doc.id,
            version_no=doc.latest_version_no
        ).first()
        if latest_ver:
            latest_ver.status = STATUS_APPROVED

    db.commit()

    cand = db.query(DBCandidate).filter(DBCandidate.candidate_id == doc.candidate_id).first()
    if cand:
        notif = _notif_document_status(doc, STATUS_APPROVED)
        _notify_candidate_sync(doc.candidate_id, notif)

    return {"message": "Document approved", "candidate_id": candidate_id, "category": category, "sub_type": sub_type}


@router.put("/{candidate_id}/{category}/{sub_type}/reject")
def reject_doc(candidate_id: str, category: str, sub_type: str, background_tasks: BackgroundTasks, reason: Optional[str] = None, db: Session = Depends(get_db)):
    """Reject a candidate document and send notification."""
    doc = db.query(CandidateDocuments).filter_by(
        candidate_id=candidate_id, category_code=category, sub_type_code=sub_type
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.latest_status = STATUS_REJECTED
    doc.rejection_reason = reason

    if doc.latest_version_no:
        latest_ver = db.query(DocumentVersion).filter_by(
            document_id=doc.id,
            version_no=doc.latest_version_no
        ).first()
        if latest_ver:
            latest_ver.status = STATUS_REJECTED

    db.commit()

    # Email notification
    cand = db.query(DBCandidate).filter(DBCandidate.candidate_id == doc.candidate_id).first()
    to_email = getattr(cand, "email", None)
    cand_name = _human_name(getattr(cand, "first_name", None), getattr(cand, "last_name", None), "Candidate")

    subject = f"[Final Decision] Document Rejected – {doc.category_code}/{doc.sub_type_code}"
    reason_line = f"\nReason: {reason}\n" if reason else "\nReason: Not specified\n"
    body = (
        f"Dear {cand_name},\n\n"
        f"Your uploaded document has been rejected.\n\n"
        f"Candidate ID: {doc.candidate_id}\n"
        f"Document: {doc.category_code} / {doc.sub_type_code}\n"
        f"Status: {doc.latest_status}\n"
        f"{reason_line}"
        "You will not be able to re-upload this document again.\n\n"
        "Regards,\nHR Team"
    )

    background_tasks.add_task(_send_mail, to_email, subject, body)

    if cand:
        notif = _notif_document_status(doc, STATUS_REJECTED, reason)
        _notify_candidate_sync(doc.candidate_id, notif)

    return {
        "message": "Rejected and email notification queued",
        "candidate_id": candidate_id,
        "category": category,
        "sub_type": sub_type,
        "reason": reason,
    }

@router.put("/{candidate_id}/{category}/{sub_type}/refer-back")
def refer_back_doc(candidate_id: str, category: str, sub_type: str, remarks: Optional[str] = None, db: Session = Depends(get_db)):
    """Refer a document back to candidate for correction."""
    doc = db.query(CandidateDocuments).filter_by(
        candidate_id=candidate_id, category_code=category, sub_type_code=sub_type
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Update candidate_documents
    doc.latest_status = STATUS_REFERRED_BACK
    doc.rejection_reason = remarks

    # 🔥 Update document_versions EXACTLY same as latest_status
    if doc.latest_version_no:
        latest_ver = db.query(DocumentVersion).filter_by(
            document_id=doc.id,
            version_no=doc.latest_version_no
        ).first()
        if latest_ver:
            latest_ver.status = STATUS_REFERRED_BACK

    db.commit()

    cand = db.query(DBCandidate).filter(DBCandidate.candidate_id == doc.candidate_id).first()
    if cand:
        notif = _notif_document_status(doc, STATUS_REFERRED_BACK, remarks)
        _notify_candidate_sync(doc.candidate_id, notif)

    return {
        "message": "Document referred back",
        "candidate_id": candidate_id,
        "category": category,
        "sub_type": sub_type,
        "remarks": remarks,
    }

