from __future__ import annotations
import os
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from decimal import Decimal
from app.utils.offersalary_breakup import compute_salary_components

from app.models.offer_tables import MgrOfferLetter, MgrPdfTemplate
from app.models.bgv import BGVForm, BGVStatus
from app.models.user_models import CandidateProfileInformation
from generate_qrcode import generate_qr_with_token
from styled_offerletter import generate_styled_offer_letter

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------------
# Reference number generator
# -----------------------------
def generate_reference_number(db: Session, model, column_name: str = "reference_number") -> str:
    """
    Generate reference number in pattern SX-YYYY-XXXX.
    Auto-increments per year.
    """
    current_year = datetime.utcnow().year
    prefix = f"SX-{current_year}"

    last_offer = (
        db.query(model)
        .filter(getattr(model, column_name).like(f"{prefix}-%"))
        .order_by(getattr(model, column_name).desc())
        .first()
    )

    new_seq = 1
    if last_offer:
        try:
            last_ref = getattr(last_offer, column_name)
            last_seq = int(last_ref.split("-")[-1])
            new_seq = last_seq + 1
        except (ValueError, AttributeError):
            pass

    return f"{prefix}-{new_seq:04d}"


# -----------------------------
# Internal helper: check candidate & BGV
# -----------------------------
def _selected_candidate(db: Session, candidate_id: str) -> CandidateProfileInformation:
    cand = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id).first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")

    bgv = db.query(BGVForm).filter(BGVForm.candidate_id == candidate_id).first()
    if not bgv or bgv.status != BGVStatus.APPROVED:
        raise HTTPException(status_code=400, detail="BGV not approved for this candidate")
    return cand


# -----------------------------
# Coercion helpers (robust to JSON)
# -----------------------------
def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    # Expect "YYYY-MM-DD"
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {v}. Use YYYY-MM-DD.")


def _to_decimal(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    try:
        return v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid number: {v}")


def _normalize_period(value: Any, *, default: str = "6 Months", unit_if_int: str = "days") -> str:
    """
    Accepts: 60, "60", "60 days", "6 months", "30 Months" -> normalized human string.
    If value is falsy -> default.
    """
    if value is None or str(value).strip() == "":
        return default
    s = str(value).strip()
    try:
        n = int(s)
        return f"{n} {unit_if_int}"
    except ValueError:
        # already textual; collapse spaces
        return " ".join(s.split())


def _fmt_money(v: Decimal) -> str:
    # simple 1,234,567 format (not Indian grouping)
    return f"{v:,.0f}"


def create_and_generate_offer(
        db: Session,
        candidate_id: str,
        *,
        template_id: int,
        offer_date: datetime,
        offer_time: str,
        annual_ctc: Decimal,
        variable_pay: Decimal,
        position: str,
        band: str,
        regime: str,
        joining_date: datetime,
        acceptance_deadline: datetime,
        reporting_time: str,
        reporting_person: str,
        reporting_office_address: str,
        reporting_contact_number: str,
        co_founder_and_director: str,
        office_timings: str,
        office_name: str,
        office_location: str,
        probation_period: str,
        notice_period: str,
        status: str = "Pending",
        base_url: str = "",
        show_salary_breakup: bool = True,  # ✅ ADD
):
    cand = _selected_candidate(db, candidate_id)
    template = db.query(MgrPdfTemplate).filter(MgrPdfTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    profile = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Candidate address not found")

    # Generate token and QR code
    token, qr_s3_url, qr_s3_key = generate_qr_with_token(base_url=base_url)
    logger.info(f"Generated token: {token}, QR S3 URL: {qr_s3_url}, QR S3 Key: {qr_s3_key}")

    # Generate reference number
    from app.utils.createtemporary_id import generate_temporary_employee_id
    reference_number = generate_temporary_employee_id()

    # Create offer record
    offer = MgrOfferLetter(
        reference_number=reference_number,
        candidate_id=candidate_id,
        template_id=template_id,
        offer_date=str(offer_date),
        offer_time=offer_time,
        annual_ctc=float(annual_ctc),
        variable_pay=float(variable_pay) if variable_pay else None,
        position=position or getattr(cand, "position_applied", None),
        band=band or (getattr(cand, "meta", {}) or {}).get("band"),
        regime=regime or (getattr(cand, "meta", {}) or {}).get("regime", "new"),
        joining_date=str(joining_date),
        acceptance_deadline=str(acceptance_deadline) if acceptance_deadline else None,
        reporting_time=reporting_time,
        reporting_person=reporting_person,
        reporting_office_address=reporting_office_address,
        reporting_contact_number=reporting_contact_number,
        co_founder_and_director=co_founder_and_director,
        office_timings=office_timings,
        probation_period=probation_period,
        notice_period=int(notice_period),
        office_name=office_name,
        office_location=office_location,
        token=token,
        qr_path=qr_s3_url,
        pdf_path="",  # Placeholder

    )

    db.add(offer)
    db.commit()
    db.refresh(offer)

    # ------------------------------------------------
    # Salary breakup (REQUIRED for PDF)
    # ------------------------------------------------
    if show_salary_breakup:
        salary = compute_salary_components(
            annual_ctc=float(offer.annual_ctc),
            regime=offer.regime
        )
    else:
        salary = {}

    # Prepare data for PDF
    data = {
        "offer_id": str(offer.id),
        "offer_date": offer.offer_date,
        "offer_time": offer.offer_time,
        "reference_number": str(reference_number),
        "name": f"{cand.first_name} {cand.last_name}",
        "address": f"{profile.address}",
        "phone_number": getattr(cand, "contact_number", ""),
        "email": cand.email,
        "position": offer.position or "",
        "office_name": offer.office_name,
        "joining_date": offer.joining_date,
        "reporting_person": offer.reporting_person,
        "reporting_contact_number": offer.reporting_contact_number,
        "reporting_time": offer.reporting_time,
        "reporting_office_address": offer.reporting_office_address,
        "band": offer.band,
        "probation_period": offer.probation_period,
        "notice_period": str(offer.notice_period),
        "acceptance_deadline": offer.acceptance_deadline or "",
        "co_founder_and_director": offer.co_founder_and_director,
        "office_timings": offer.office_timings,
        "annual_ctc": f"{offer.annual_ctc}",
        "variable_pay": f"{(offer.variable_pay or 0)}",
        "regime": offer.regime,
        "office_location": offer.office_location,
        "token": offer.token,
        "qr_path": qr_s3_url,  # Pass QR S3 URL to PDF generator
        "show_salary_breakup": show_salary_breakup,

        # --------------------------------------------------
        # CTC SUMMARY (SAFE)
        # --------------------------------------------------
        "annual_ctc": salary.get("annual_ctc", ""),
        "monthly_ctc": salary.get("monthly_ctc", ""),

        # --------------------------------------------------
        # EARNINGS (SAFE)
        # --------------------------------------------------
        "basic_annual": salary.get("basic_annual", ""),
        "basic_monthly": salary.get("basic_monthly", ""),

        "hra_annual": salary.get("hra_annual", ""),
        "hra_monthly": salary.get("hra_monthly", ""),

        "food_annual": salary.get("food_annual", ""),
        "food_monthly": salary.get("food_monthly", ""),

        "special_annual": salary.get("special_annual", ""),
        "special_monthly": salary.get("special_monthly", ""),

        "other_annual": salary.get("other_annual", ""),
        "other_monthly": salary.get("other_monthly", ""),

        # --------------------------------------------------
        # DEDUCTIONS (SAFE)
        # --------------------------------------------------
        "pf_annual": salary.get("pf_annual", ""),
        "pf_monthly": salary.get("pf_monthly", ""),

        "pt_annual": salary.get("pt_annual", ""),
        "pt_monthly": salary.get("pt_monthly", ""),

        "tds_annual": salary.get("tds_annual", ""),
        "tds_monthly": salary.get("tds_monthly", ""),

        "health_annual": salary.get("health_annual", ""),
        "health_monthly": salary.get("health_monthly", ""),

        # --------------------------------------------------
        # NET PAY (SAFE)
        # --------------------------------------------------
        "net_annual": salary.get("net_annual", ""),
        "net_monthly": salary.get("net_monthly", ""),
    }

    # Generate PDF with QR
    file_name = f"offers/OfferLetter_{cand.first_name}_{cand.last_name}_{offer.reference_number}.pdf"
    pdf_bytes = generate_styled_offer_letter(
        template_path=template.file_path,
        data=data
    )
    import boto3
    from app.utils.configure import AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET

    # Upload PDF to S3
    s3_client = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )
    try:
        s3_client.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=file_name,
            Body=pdf_bytes,
            ContentType="application/pdf"
        )
        s3_url = f"https://{AWS_S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{file_name}"
        offer.pdf_path = s3_url
        db.add(offer)
        db.commit()
        db.refresh(offer)
    except Exception as e:
        logger.error(f"Failed to upload PDF to S3: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to upload PDF to S3: {str(e)}")

    # Construct response
    response_data = {
        "id": offer.id,
        "candidate_id": offer.candidate_id,
        "offer_date": offer.offer_date,
        "offer_time": offer.offer_time,
        "joining_date": offer.joining_date,
        "reporting_time": offer.reporting_time,
        "reporting_person": offer.reporting_person,
        "reporting_contact_number": offer.reporting_contact_number,
        "reporting_office_address": offer.reporting_office_address,
        "position": offer.position,
        "band": offer.band,
        "annual_ctc": offer.annual_ctc,
        "variable_pay": offer.variable_pay,
        "reference_number": reference_number,
        "acceptance_deadline": offer.acceptance_deadline,
        "co_founder_and_director": offer.co_founder_and_director,
        "office_timings": offer.office_timings,
        "notice_period": offer.notice_period,
        "probation_period": offer.probation_period,
        "regime": offer.regime,
        "office_name": offer.office_name,
        "office_location": offer.office_location,
        "pdf_path": offer.pdf_path,
        "created_at": offer.created_at,
        "token": offer.token,
        "qr_path": qr_s3_url,

    }
    logger.info(f"Generated offer response: {response_data}")
    return response_data

