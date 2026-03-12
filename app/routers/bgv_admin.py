import os
import io
import asyncio
import requests
from datetime import datetime
from typing import Literal, Optional, Tuple, List
from fastapi import (
    APIRouter, Depends, HTTPException, Query,
    UploadFile, File, status, BackgroundTasks
)
from app.utils.configure import AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.utils.helper import require_hr_or_superadmin

from app.models.bgv import (
    BGVForm,BGVStatus
)
from app.models.offer_tables import MgrOfferLetter, MgrPdfTemplate
from app.models.user_models import CandidateProfileInformation as Candidate

# Schemas/services
from app.schemas.bgv import BGVFormOut, BGVAdminReviewIn
from app.schemas.offer_bgv_schemas import (
    MgrPdfTemplateOut,
    MgrOfferLetterCreate,
    MgrOfferLetterOut
)

from app.services.offer_service import create_and_generate_offer
from app.schemas.user_schemas import NotificationStructure
from app.utils.notify_depends import send_notification_to_candidate as _notify_candidate_async
from app.utils.s3_utils import _upload_to_s3, generate_http_url
from app.utils.aws_email import aws_send_mail
import logging
import uuid
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
import boto3
import pytz
from urllib.parse import urlparse

# Load environment variables
load_dotenv()
IST = pytz.timezone("Asia/Kolkata")

router = APIRouter(
    prefix="/bgv/admin",
    tags=["BGV – Admin"],
    dependencies=[Depends(require_hr_or_superadmin)]
)

logger = logging.getLogger("offer_email")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


# =============================================================================
#                         NOTIFICATION HELPERS (ADDED)
# =============================================================================

logger = logging.getLogger(__name__)

def _notify_candidate_sync(candidate_id: str, notif: NotificationStructure) -> None:
    """Sync-safe wrapper to send in-app notifications to candidates."""
    payload = notif.model_dump() if hasattr(notif, "model_dump") else notif.dict()

    async def _run():
        try:
            await _notify_candidate_async(candidate_id, payload)
        except Exception as e:
            logger.error(
                f"Failed to notify candidate {candidate_id}: {e}",
                exc_info=True
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        asyncio.run(_run())


def _notif_bgv_status(
    form: BGVForm,
    status: str,
    remarks: Optional[str] = None
) -> NotificationStructure:
    """
    Generate structured in-app notification for candidate
    when BGV admin reviews the form.
    """

    scenario = f"bgv_{status.lower()}"

    # Human-friendly message
    if status == "APPROVED":
        message = "Your BGV verification has been approved."
    elif status == "REJECTED":
        message = "Your BGV verification has been rejected."
    elif status == "REFERRED_BACK":
        message = "Your BGV verification requires corrections."
    else:
        message = f"Your BGV verification status updated to {status}."

    data = {
        "candidate_id": form.candidate_id,
        "form_id": form.id,
        "status": status,
        "remarks": remarks or "",
        "updated_at_utc": datetime.utcnow().isoformat(),
    }

    return NotificationStructure(
        scenario=scenario,
        message=message,
        data=data
    )


def _notif_offer_generated(offer: dict) -> NotificationStructure:
    """Generate structured notification payload when offer is generated."""
    scenario = "offer_generated"

    message = (
        f"Congratulations! Your offer letter "
        f"(Ref: {offer['reference_number']}) has been generated."
    )

    return NotificationStructure(
        scenario=scenario,
        message=message,
        meta_data={
            # FROM SWAGGER SCREENSHOT
            "routh_path": f"/candidate/offer/download/{offer['id']}",
            "http_method": "GET",
        },
        data={
            "candidate_id": offer["candidate_id"],
            "offer_id": offer["id"],
            "reference_number": offer["reference_number"],
            "designation": offer.get("designation", offer.get("position")),
            "joining_date": str(offer.get("joining_date", "")),
            "annual_ctc": str(offer.get("annual_ctc", "")),
            "updated_at_utc": datetime.utcnow().isoformat(),
        },
    )



# -----------------------------------------------------------------------------
# Email helpers – SES
# -----------------------------------------------------------------------------
async def _send_offer_letter_email_async(
        receiver_email: str,
        candidate_name: Optional[str],
        offer: MgrOfferLetter,
) -> Tuple[bool, Optional[str]]:
    """Send offer letter email with PDF attachment using SendGrid."""

    SES_HOST = os.getenv("SES_HOST")
    SES_PORT = os.getenv("SES_PORT")

    SES_USER = os.getenv("SES_USER")
    SES_PASS = os.getenv("SES_PASS")

    EMAIL_FROM = os.getenv(" EMAIL_FROM", "careers@securxperts.com")
    EMAIL_FROM_NAME = "SecurXperts"
    name = candidate_name or "Candidate"

    # Validate required keys
    required_keys = ["reference_number", "annual_ctc", "joining_date", "notice_period"]
    missing_keys = [key for key in required_keys if key not in offer]
    if missing_keys:
        raise ValueError(f"Offer dictionary missing keys: {missing_keys}")

    subject = f"Offer Letter Generated – Ref: {offer['reference_number']}"
    body = "\n".join([
        f"Dear {name},",
        "",
        "Congratulations! Your offer letter has been generated.",
        "",
        f"Reference Number : {offer['reference_number']}",
        f"Designation      : {offer.get('designation', offer.get('position', '-'))}",
        f"Annual CTC       : {offer['annual_ctc']}",
        f"Joining Date     : {offer['joining_date']}",
        f"Reporting Time   : {offer.get('reporting_time', '-')}",
        f"Reporting Person : {offer.get('reporting_person', '-')}",
        f"Notice Period    : {offer['notice_period']}",
        "",
        "Please log in to the portal to view and download your PDF offer letter.",
        "If you have any questions, reply to this email.",
        "",
        "Regards,",
        "HR Team",
    ])

    try:

        if 'pdf_path' not in offer:
            raise ValueError("Offer dictionary missing 'pdf_path' key")

        # Fetch PDF bytes from HTTP URL
        response = requests.get(offer['pdf_path'])
        response.raise_for_status()  # raise error if download fails

        file_bytes = io.BytesIO(response.content)

        # Create UploadFile for FastAPI
        attachment_file = UploadFile(
            filename="OfferLetter.pdf",
            file=file_bytes
        )

        await aws_send_mail(
            from_email=EMAIL_FROM,
            reciver_to=[receiver_email],
            subject=subject,
            body=body,
            body_type="plain",
            attachment_objs=[attachment_file],
        )
        logger.info(f"Email successfully sent to: {receiver_email}")
        return True, None
    except Exception as e:
        logger.error(f"Failed to send email to {receiver_email}: {e}", exc_info=True)
        return False, str(e)


def _send_offer_letter_email_sync(receiver_email: str, candidate_name: str, offer_obj: MgrOfferLetter) -> None:
    """BackgroundTasks calls sync functions; run the async mailer in its own loop."""
    import asyncio
    try:
        asyncio.run(_send_offer_letter_email_async(receiver_email, candidate_name, offer_obj))
    except Exception as e:
        logger.error(f"Background email task crashed for {receiver_email}: {e}", exc_info=True)


logger = logging.getLogger(__name__)

def _ensure_pdf_exists(offer: MgrOfferLetter, db: Session) -> None:
    """
    Ensure there’s a valid PDF path in S3.
    If missing, generate a basic fallback PDF and upload to S3.
    """
    try:
        s3_client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY
        )

        # Check if PDF already exists in S3
        if offer.pdf_path:
            s3_key = offer.pdf_path.split(f"https://{AWS_S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/")[-1]
            try:
                s3_client.head_object(Bucket=AWS_S3_BUCKET, Key=s3_key)
                return  # PDF exists in S3
            except s3_client.exceptions.ClientError:
                logger.warning("PDF not found in S3; generating fallback.")

        # Generate basic PDF in memory
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        y = h - 40

        def line(txt: str, step: int = 16):
            nonlocal y
            c.drawString(30, y, (txt or "")[:120])
            y -= step
            if y < 60:
                c.showPage()
                y = h - 40

        c.setTitle(f"Offer_{offer.reference_number}")
        c.setFont("Helvetica-Bold", 14)
        line("Offer Letter")
        c.setFont("Helvetica", 10)
        line(" ")
        line(f"Reference : {offer.reference_number}")
        line(f"Candidate ID : {offer.candidate_id}")
        line(f"Position : {offer.position}")
        line(f"Annual CTC : {offer.annual_ctc}")
        line(f"Joining Date : {offer.joining_date}")
        line(f"Reporting Time : {getattr(offer, 'reporting_time', '-')}")
        line(f"Reporting Pers.: {getattr(offer, 'reporting_person', '-')}")
        line(f"Office : {getattr(offer, 'office_name', '')}, {getattr(offer, 'office_location', '')}")
        line(" ")
        line("This is an auto-generated copy used for download/email fallback.")
        c.showPage()
        c.save()
        buf.seek(0)

        # Upload PDF to S3
        s3_key = f"offers/Offer_{offer.reference_number}.pdf"
        s3_client.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=s3_key,
            Body=buf,
            ContentType="application/pdf"
        )
        offer.pdf_path = f"https://{AWS_S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        db.add(offer)
        db.commit()
        db.refresh(offer)

        logger.info(f"Fallback PDF uploaded to S3: {offer.pdf_path}")

    except Exception as e:
        logger.error(f"_ensure_pdf_exists failed for offer {offer.id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate/upload fallback PDF: {str(e)}")

def bgv_form_to_out(form):

    educations = []
    for e in form.educations:
        educations.append({
            "id": e.id,
            "qualification": e.qualification,
            "institution_name": e.institution_name,
            "university_or_board": e.university_or_board,
            "year_of_passing": e.year_of_passing,
            "percentage_or_cgpa": e.percentage_or_cgpa,
        })

    employments = []
    for emp in form.employments:
        employments.append({
            "id": emp.id,
            "company_name": emp.company_name,
            "designation": emp.designation,
            "start_date": emp.start_date.strftime("%m/%Y") if emp.start_date else None,
            "end_date": emp.end_date.strftime("%m/%Y") if emp.end_date else None,
            "reason_for_leaving": emp.reason_for_leaving,
            "verifiers": emp.verifiers or [],
        })

    references = []
    for r in form.references:
        references.append({
            "id": r.id,
            "name": r.name,
            "designation": r.designation,
            "company": r.company,
            "relationship": r.relationship,
            "contact_number": r.contact_number,
            "email": r.email,
        })

    return {
        "id": form.id,
        "candidate_id": form.candidate_id,

        # --- Personal info ---
        "full_name": form.full_name,
        "father_name": form.father_name,
        "mother_name": form.mother_name,
        "spouse_name": form.spouse_name,
        "dob": form.dob,
        "gender": form.gender,
        "nationality": form.nationality,
        "marital_status": form.marital_status,
        "aadhaar_number": form.aadhaar_number,
        "pan_number": form.pan_number,
        "uan_number": form.uan_number,
        "passport_number": form.passport_number,
        "provident_fund_number": form.provident_fund_number,
        "permanent_address": form.permanent_address,
        "current_address": form.current_address,
        "contact_number": form.contact_number,
        "alternate_number": form.alternate_number,
        "email": form.email,

        # --- Emergency Contact ---
        "emergency_contact_relationship": form.emergency_contact_relationship,
        "emergency_contact_name": form.emergency_contact_name,
        "emergency_contact_number": form.emergency_contact_number,

        # --- Checklist booleans ---
        "has_aadhaar": form.has_aadhaar,
        "has_pan": form.has_pan,
        "has_passport": form.has_passport,
        "has_10_12_grad_pg": form.has_10_12_grad_pg,
        "has_relieving_letters": form.has_relieving_letters,
        "has_experience_letters": form.has_experience_letters,
        "has_latest_salary_slip": form.has_latest_salary_slip,
        "has_bank_statement_3m": form.has_bank_statement_3m,
        "has_resume": form.has_resume,

        # --- Admin review ---
        "status": form.status.value,
        "admin_remarks": form.admin_remarks,
        "reviewed_by_admin_id": form.reviewed_by_admin_id,
        "reviewed_at": form.reviewed_at,

        # --- Signature ---
        "signature_file_key": form.signature_file_key,
        "signature_mime_type": form.signature_mime_type,
        "signature_size_bytes": form.signature_size_bytes,

        # --- Relations ---
        "educations": educations,
        "employments": employments,
        "references": references,

        # --- Timestamps ---
        "created_at": form.created_at,
        "updated_at": form.updated_at,
    }

# -------------------------------------------------------------------
# List BGV forms
# -------------------------------------------------------------------
@router.get("/list", response_model=List[BGVFormOut])
def list_forms(
    status_filter: str | None = Query(None),
    candidate_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(BGVForm)
    if status_filter:
        q = q.filter(BGVForm.status == status_filter)
    if candidate_id:
        q = q.filter(BGVForm.candidate_id == candidate_id)

    forms = q.order_by(BGVForm.created_at.desc()).all()

    return [bgv_form_to_out(f) for f in forms]


@router.get("/templateslist")
def list_templates(db: Session = Depends(get_db)):
    templates = db.query(MgrPdfTemplate).all()
    results = []
    for t in templates:
        presigned_url = generate_http_url(t.file_path, expires_in=3600)
        results.append({
            "id": t.id,
            "letter_type": t.letter_type,
            "file_path": t.file_path,
            "url": presigned_url
        })
    return results

@router.get("/offers", response_model=List[MgrOfferLetterOut])
def list_all_offers(candidate_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(MgrOfferLetter)
    if candidate_id:
        q = q.filter(MgrOfferLetter.candidate_id == candidate_id)
    return q.order_by(MgrOfferLetter.created_at.desc()).all()

@router.get("/{form_id}", response_model=BGVFormOut)
def get_form(form_id: int, db: Session = Depends(get_db)):
    form = db.get(BGVForm, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    return bgv_form_to_out(form)

@router.post("/{form_id}/review", response_model=BGVFormOut)
def review_form(form_id: int, review: BGVAdminReviewIn, db: Session = Depends(get_db)):
    form = db.get(BGVForm, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    form.status = review.status
    form.admin_remarks = review.admin_remarks
    form.reviewed_at = datetime.utcnow()
    form.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(form)

    for emp in form.employments or []:
        if emp.start_date and "-" in str(emp.start_date):
            try:
                emp.start_date = datetime.strptime(str(emp.start_date), "%Y-%m-%d").strftime("%m/%Y")
            except:
                pass
        if emp.end_date and "-" in str(emp.end_date):
            try:
                emp.end_date = datetime.strptime(str(emp.end_date), "%Y-%m-%d").strftime("%m/%Y")
            except:
                pass

    for edu in form.educations or []:
        if not hasattr(edu, "id"):
            edu.id = None
    for emp in form.employments or []:
        if not hasattr(emp, "id"):
            emp.id = None
    for ref in form.references or []:
        if not hasattr(ref, "id"):
            ref.id = None

    notif = _notif_bgv_status(form, review.status.value, review.admin_remarks)
    _notify_candidate_sync(form.candidate_id, notif)

    return form


@router.get("/{form_id}/download")
def download_form_pdf(form_id: int, db: Session = Depends(get_db)):
    form = db.get(BGVForm, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        y = h - 40

        def line(txt: str, step: int = 16):
            nonlocal y
            c.drawString(30, y, txt[:120]);
            y -= step
            if y < 60:
                c.showPage();
                y = h - 40

        c.setTitle(f"BGV_{form.candidate_id}.pdf")
        c.setFont("Helvetica-Bold", 14);
        line("Background Verification Form")
        c.setFont("Helvetica", 10)
        line(f"Candidate ID: {form.candidate_id} | Status: {form.status}")
        line(f"Name: {form.full_name or ''}")
        line(f"DOB: {form.dob or ''} Gender: {form.gender or ''} Nationality: {form.nationality or ''}")
        line(f"Contact: {form.contact_number or ''} Email: {form.email or ''}")
        line(f"Current Addr: {form.current_address or ''}")
        line(f"Permanent Addr: {form.permanent_address or ''}")
        line(" ")
        line("Education:")
        for e in (form.educations or []):
            line(
                f" - {e.qualification} | {e.institution_name} | {e.university_or_board} | {e.year_of_passing} | {e.percentage_or_cgpa}")
        line(" ")
        line("Employment:")
        for emp in (form.employments or []):
            line(f" - {emp.company_name} | {emp.designation} | {emp.start_date}-{emp.end_date}")
        line(" ")
        line("References:")
        for r in (form.references or []):
            line(
                f" - {r.name} | {r.designation} @ {r.company} | Rel: {r.relationship} | {r.contact_number} | {r.email}")
        line(" ")
        line("Checklist:")
        line(
            f"Aadhaar: {form.has_aadhaar} PAN: {form.has_pan} Passport: {form.has_passport} 10/12/Grad/PG: {form.has_10_12_grad_pg}")
        line(
            f"Relieving: {form.has_relieving_letters} Experience: {form.has_experience_letters} Payslip: {form.has_latest_salary_slip}")
        line(f"BankStmt(3m): {form.has_bank_statement_3m} Resume: {form.has_resume}")
        if form.admin_remarks:
            line(" ");
            line(f"Admin Remarks: {form.admin_remarks}")
        line(" ");
        line(f"Reviewed At: {form.reviewed_at or ''}")
        c.showPage();
        c.save()
        buf.seek(0)
        filename = f"BGV_{form.candidate_id}.pdf"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception:
        raise HTTPException(status_code=501, detail="PDF generator unavailable on this server")


@router.post("/upload", response_model=MgrPdfTemplateOut)
def upload_template(
        name: Literal["offer_letter"],
        file: UploadFile = File(...),
        db: Session = Depends(get_db)
):
    try:
        file_bytes = file.file.read()

        # Create unique key
        key = f"templates/{uuid.uuid4()}-{file.filename}"

        # Upload to S3
        _upload_to_s3(
            file_bytes=file_bytes,
            key=key,
            content_type=file.content_type
        )

        # Store only the key in DB
        template = MgrPdfTemplate(letter_type=name, file_path=key)
        db.add(template)
        db.commit()
        db.refresh(template)

        # Also return a presigned URL for immediate frontend preview
        url = generate_http_url(key, expires_in=3600)

        return {
            **template.__dict__,
            "preview_url": url  # temporary presigned URL for download/preview
        }
    except Exception as e:
        db.rollback()
        raise RuntimeError(f"Upload failed: {e}")
    finally:
        file.file.seek(0)


# -----------------------------------------------------------------------------
# Offer generation + email (robust)
# -----------------------------------------------------------------------------
@router.post("/generate/{candidate_id}", response_model=MgrOfferLetterOut)
async def generate_offer(
        candidate_id: str,
        payload: MgrOfferLetterCreate,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
):
    bgv = db.query(BGVForm).filter(BGVForm.candidate_id == candidate_id).first()
    if not bgv:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="BGV record not found for candidate")
    if bgv.status != BGVStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Offer letter cannot be generated. Current BGV status: {bgv.status}"
        )

    existing_offer = db.query(MgrOfferLetter).filter(MgrOfferLetter.candidate_id == candidate_id).first()
    if existing_offer:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="An offer letter already exists for this candidate")

    offer = create_and_generate_offer(
        db,
        candidate_id,
        template_id=payload.template_id,
        offer_date=payload.offer_date,
        offer_time=(payload.offer_time or ""),
        annual_ctc=(payload.annual_ctc or 0),
        variable_pay=(payload.variable_pay or 0),
        position=payload.position,
        band=(payload.band or ""),
        regime=(payload.regime),
        joining_date=payload.joining_date,
        acceptance_deadline=payload.acceptance_deadline,
        reporting_time=(payload.reporting_time or ""),
        reporting_person=(payload.reporting_person or ""),
        reporting_office_address=(payload.reporting_office_address or ""),
        reporting_contact_number=(payload.reporting_contact_number or ""),
        co_founder_and_director=(payload.co_founder_and_director or ""),
        office_timings=(payload.office_timings or ""),
        office_name=(payload.office_name or ""),
        office_location=(payload.office_location or ""),
        probation_period=(str(payload.probation_period) if payload.probation_period not in (None, "") else "6 Months"),
        notice_period=(str(payload.notice_period) if payload.notice_period not in (None, "") else "60 days"),
        show_salary_breakup=payload.show_salary_breakup,  # ADD
    )

    cand = db.query(Candidate).filter(Candidate.candidate_id == candidate_id).first()
    if cand and cand.email:
        full_name = (cand.first_name or "") + (f" {cand.last_name}" if cand.last_name else "")
        background_tasks.add_task(_send_offer_letter_email_sync, cand.email, full_name.strip(), offer)
        logger.info("Queued offer email to %s", cand.email)

        # In-app notification for offer generation
        notif = _notif_offer_generated(offer)
        _notify_candidate_sync(candidate_id, notif)
    else:
        logger.info("No candidate email found for %s; skipping email", candidate_id)

    return offer


# -----------------------------------------------------------------------------
# Offer listing & downloads
# -----------------------------------------------------------------------------


@router.get("/download/{offer_id}")
def download_offer(offer_id: int, db: Session = Depends(get_db)):
    logger.info(f"Download request for offer_id: {offer_id}")
    try:
        offer = db.query(MgrOfferLetter).filter(MgrOfferLetter.id == offer_id).first()
        if not offer:
            logger.error(f"No offer found for ID {offer_id} in database")
            raise HTTPException(status_code=404, detail=f"Offer with ID {offer_id} not found")

        if not offer.pdf_path:
            logger.error(f"PDF path is empty for offer ID {offer_id}")
            raise HTTPException(status_code=404, detail="Offer PDF path not specified")

        pdf_url = offer.pdf_path
        logger.info(f"Streaming PDF from S3 URL: {pdf_url}")

        # Stream the PDF from S3 URL
        response = requests.get(pdf_url, stream=True)
        if response.status_code != 200:
            logger.error(f"Failed to fetch PDF from S3: {response.status_code}")
            raise HTTPException(status_code=500, detail="Failed to download PDF from S3")

        filename = f"OfferLetter_{offer.reference_number}.pdf"
        return StreamingResponse(
            response.raw,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"Error in download_offer for ID {offer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/verify-offer/{token}")
async def verify_offer(token: str, db: Session = Depends(get_db)):
    """
    Verify an offer letter using a token, stream the PDF from S3, and return verification details.
    Works whether pdf_path is stored as a full S3 URL or just a key.
    """
    ist_now = datetime.now(IST)
    logger.info(f"Verification attempt for token: {token} at {ist_now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    try:
        # Query offer by token
        offer = db.query(MgrOfferLetter).filter(MgrOfferLetter.token == token).first()
        if not offer:
            logger.warning(f"Invalid token: {token} not found in database")
            raise HTTPException(status_code=404, detail="Invalid offer letter")

        if offer.candidate_status == "VIEWED":
            logger.warning(f"Offer {offer.reference_number} already verified")
            raise HTTPException(status_code=400, detail="Offer already verified")

        # Mark as verified
        offer.candidate_status = "VIEWED"
        db.commit()
        db.refresh(offer)
        logger.info(f"Offer {offer.reference_number} marked as verified at {ist_now.strftime('%Y-%m-%d %H:%M:%S IST')}")

        # Fetch PDF from S3
        if not offer.pdf_path:
            logger.error(f"PDF path missing for offer ID {offer.id}")
            raise HTTPException(status_code=404, detail="Offer PDF not available")

        # Handle both full URL and plain key
        if offer.pdf_path.startswith("http"):
            parsed = urlparse(offer.pdf_path)
            s3_key = parsed.path.lstrip("/")  # Extract /bucket/key -> key
        else:
            s3_key = offer.pdf_path

        s3_client = boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
        )

        try:
            s3_object = s3_client.get_object(Bucket=os.getenv("S3_BUCKET"), Key=s3_key)
        except boto3.client.exceptions.NoSuchKey:
            logger.error(f"S3 key not found: {s3_key}")
            raise HTTPException(status_code=404, detail="Offer PDF not found in S3")
        except Exception as e:
            logger.error(f"S3 access error for key {s3_key}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to access S3")

        filename = f"OfferLetter_{offer.reference_number}.pdf"
        pdf_stream = s3_object["Body"]

        # Prepare verification response
        verification_response = {
            "status": "verified",
            "reference_number": offer.reference_number,
            "token": token,
            "verified_at": ist_now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "pdf_available": True,
            "qr_code_status": "valid" if offer.qr_path else "missing"
        }

        # Stream the PDF with JSON metadata
        return StreamingResponse(
            pdf_stream,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={filename}",
                "X-Verification-Response": str(verification_response)
            }
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Internal error in verify_offer: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="check in verify router")


@router.get("/admin-view/{candidate_id}")
def view_candidate_offerletter(candidate_id: str, db: Session = Depends(get_db)):
    offer = db.query(MgrOfferLetter).filter(MgrOfferLetter.candidate_id == candidate_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="offer letter not found")
    return offer
