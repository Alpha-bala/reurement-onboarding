# app/routers/otp.py
import os
import random
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user_models import OTPRequest
from app.utils.aws_email import aws_send_mail  # ✅ CHANGED: use AWS SES helper

# ---------------- Config ----------------
OTP_EXP_MINUTES = int(os.getenv("OTP_EXP_MINUTES", "5"))
RESEND_COOLDOWN_SECONDS = int(os.getenv("RESEND_COOLDOWN_SECONDS", "60"))

# AWS SES sender
FROM_EMAIL = os.getenv("SES_SENDER", "careers@securxperts.com")


def _generate_otp() -> str:
    return str(random.randint(100000, 999999))


async def _send_email_otp(receiver_email: str, otp: str, purpose: str) -> None:
    """
    Send the OTP email using AWS SES SMTP (centralized helper).
    """
    if purpose == "signup":
        subject = "Your OTP for Job Portal Registration"
        body = (
            f"Your registration OTP is: {otp}\n\n"
            f"It will expire in {OTP_EXP_MINUTES} minutes."
        )
    else:
        subject = "Your OTP for Password Reset"
        body = (
            f"Your password reset OTP is: {otp}\n\n"
            f"It will expire in {OTP_EXP_MINUTES} minutes."
        )

    try:
        await aws_send_mail(
            from_email=FROM_EMAIL,
            reciver_to=[receiver_email],
            subject=subject,
            body=body,
            body_type="plain",
        )
    except Exception as e:
        # Non-blocking failure (OTP flow should not crash API)
        print(f"[OTP email] SES send failed: {e}")


def _active_otp_request(db: Session, *, email: str, purpose: str) -> Optional[OTPRequest]:
    """
    Return the latest unverified & unexpired OTP row for (email, purpose).
    """
    return (
        db.query(OTPRequest)
        .filter(
            OTPRequest.email == email,
            OTPRequest.purpose == purpose,
            OTPRequest.verified_at.is_(None),
            OTPRequest.expires_at >= datetime.utcnow(),
        )
        .order_by(OTPRequest.expires_at.desc())
        .first()
    )


def _seconds_until(dt: datetime) -> int:
    delta = dt - datetime.utcnow()
    s = int(delta.total_seconds())
    return s if s > 0 else 0
