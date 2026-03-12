# app/routers/hr_otp.py
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from enum import Enum
from app.database import get_db
from app.models.auth_models import UserDetail, ProfileInformation, UserRole
from app.models.user_models import OTPRequest
from app.utils.helper import verify_password, get_password_hash
from app.utils.otp import (
    _generate_otp,
    _send_email_otp,
    _active_otp_request,
    RESEND_COOLDOWN_SECONDS,
    OTP_EXP_MINUTES,
    _seconds_until,
)
from app.utils.guards import hr_session_required
OTP_FRESH_MIN = 15
router = APIRouter(prefix="/auth/hr", tags=["HR-OTP"])

class HRPurpose(str, Enum):
    signup = "signup"
    reset = "reset"
    forgot = "forgot"
    email_change_old = "email_change_old"
    email_change_new = "email_change_new"

@router.get("/otp-purposes")
def get_purposes():
    return {"purposes": [p.value for p in HRPurpose]}

# ----------------------------------------------------------------------
# SEND OTP
# ----------------------------------------------------------------------
from sqlalchemy import func

@router.post("/send-otp")
async def hr_send_otp(
    email: EmailStr = Query(...),
    purpose: HRPurpose = Query(...),
    db: Session = Depends(get_db),
):
    p = purpose.value

    if p == "signup":
        exists = db.query(ProfileInformation).filter(
            func.json_extract(ProfileInformation.email, "$.primary") == email
        ).first()
        if exists:
            raise HTTPException(409, "EMAIL_EXISTS")

    if p in {"reset", "forgot", "email_change_old"}:
        exists = db.query(ProfileInformation).filter(
            func.json_extract(ProfileInformation.email, "$.primary") == email
        ).first()
        if not exists:
            raise HTTPException(404, "EMAIL_NOT_REGISTERED")

    if p == "email_change_new":
        exists = db.query(ProfileInformation).filter(
            func.json_extract(ProfileInformation.email, "$.primary") == email
        ).first()
        if exists:
            raise HTTPException(409, "NEW_EMAIL_ALREADY_EXISTS")

    now = datetime.utcnow()
    otp = _generate_otp()

    existing = _active_otp_request(db, email=email, purpose=p)

    if existing:
        cooldown_until = (
            existing.expires_at - timedelta(minutes=OTP_EXP_MINUTES)
        ) + timedelta(seconds=RESEND_COOLDOWN_SECONDS)

        if now < cooldown_until:
            return {
                "otp_request_id": existing.otp_id,
                "expires_at": existing.expires_at,
                "resend_in_seconds": _seconds_until(existing.expires_at),
            }

        existing.otp_hash = get_password_hash(otp)
        existing.attempts = 0
        existing.verified_at = None
        existing.expires_at = now + timedelta(minutes=OTP_EXP_MINUTES)

        db.commit()
        db.refresh(existing)

        await _send_email_otp(email, otp, purpose=p)

        return {
            "otp_request_id": existing.otp_id,
            "expires_at": existing.expires_at,
            "resend_in_seconds": RESEND_COOLDOWN_SECONDS,
        }

    new = OTPRequest(
        email=email,
        purpose=p,
        role="HR",
        otp_hash=get_password_hash(otp),
        attempts=0,
        max_attempts=5,
        expires_at=now + timedelta(minutes=OTP_EXP_MINUTES),
    )

    db.add(new)
    db.commit()
    db.refresh(new)
    await _send_email_otp(email, otp, purpose=p)
    return {
        "otp_request_id": new.otp_id,
        "expires_at": new.expires_at,
        "resend_in_seconds": RESEND_COOLDOWN_SECONDS,
    }

# ----------------------------------------------------------------------
# VERIFY OTP
# ----------------------------------------------------------------------
class HRVerifyIn(BaseModel):
    otp_request_id: str
    otp: str


@router.post("/verify-otp")
def hr_verify_otp(payload: HRVerifyIn, db: Session = Depends(get_db)):

    req = db.query(OTPRequest).filter(
        OTPRequest.otp_id == payload.otp_request_id
    ).first()

    if not req:
        raise HTTPException(404, "OTP_REQUEST_NOT_FOUND")

    if req.expires_at < datetime.utcnow():
        raise HTTPException(400, "OTP_EXPIRED")

    if not verify_password(payload.otp, req.otp_hash):
        req.attempts += 1
        db.commit()
        raise HTTPException(400, "OTP_INCORRECT")

    req.verified_at = datetime.utcnow()
    db.commit()

    return {
        "verified": True,
        "email": req.email,
        "purpose": req.purpose,
    }


# ----------------------------------------------------------------------
# RESEND OTP AFTER OTP EXPIRED
# ----------------------------------------------------------------------
@router.post("/resend-expired-otp")
async def resend_expired_otp(
    email: EmailStr = Query(...),
    purpose: HRPurpose = Query(...),
    db: Session = Depends(get_db),
):
    p = purpose.value
    req = db.query(OTPRequest).filter(
        OTPRequest.email == email,
        OTPRequest.purpose == p,
        OTPRequest.role == "HR"
    ).order_by(OTPRequest.otp_id.desc()).first()

    if not req:
        raise HTTPException(404, "NO_OTP_REQUEST_FOUND")

    if req.expires_at > datetime.utcnow():
        raise HTTPException(400, "OTP_NOT_EXPIRED")

    new_otp = _generate_otp()
    now = datetime.utcnow()

    req.otp_hash = get_password_hash(new_otp)
    req.attempts = 0
    req.verified_at = None
    req.expires_at = now + timedelta(minutes=OTP_EXP_MINUTES)

    db.commit()
    db.refresh(req)

    await _send_email_otp(email, new_otp, purpose=p)

    return {
        "message": "OTP resent successfully (previous OTP was expired)",
        "otp_request_id": req.otp_id,
        "expires_at": req.expires_at,
    }

# ----------------------------------------------------------------------
# FORGOT PASSWORD RESET
# ----------------------------------------------------------------------
class HRForgotPasswordReset(BaseModel):
    email: EmailStr
    otp_request_id: str
    new_password: str
    confirm_password: str

@router.post("/reset-password/forgot")
def hr_reset_password_forgot(
    payload: HRForgotPasswordReset,
    db: Session = Depends(get_db)
):
    if payload.new_password != payload.confirm_password:
        raise HTTPException(400, "PASSWORD_MISMATCH")

    otp = (
        db.query(OTPRequest)
        .filter(
            OTPRequest.otp_id == payload.otp_request_id,
            OTPRequest.email == payload.email,
            OTPRequest.purpose.in_(["forgot", "reset"]),
            OTPRequest.role == "HR",
            OTPRequest.verified_at.isnot(None),
            OTPRequest.verified_at >= datetime.utcnow() - timedelta(minutes=OTP_FRESH_MIN)
        )
        .first()
    )

    if not otp:
        raise HTTPException(400, "OTP_NOT_VERIFIED")

    ud = (
        db.query(UserDetail)
        .join(ProfileInformation)
        .filter(
            func.JSON_UNQUOTE(
                func.JSON_EXTRACT(ProfileInformation.email, "$.primary")
            ) == payload.email
        )
        .first()
    )

    if not ud:
        raise HTTPException(404, "HR_NOT_FOUND")

    pi = (
        db.query(ProfileInformation)
        .filter(ProfileInformation.employee_id == ud.employee_id)
        .first()
    )

    if not pi:
        raise HTTPException(404, "PROFILE_NOT_FOUND")

    role = (
        db.query(UserRole)
        .filter(UserRole.role_id == pi.role_id)
        .first()
    )

    if not role or role.role_name.upper() != "HR":
        raise HTTPException(403, "ONLY_HR_CAN_RESET_PASSWORD")

    ud.password = get_password_hash(payload.new_password)
    db.commit()

    return {"message": "Password reset successfully"}

# ----------------------------------------------------------------------
# RESET PASSWORD WITH OLD PASSWORD  (HR ONLY)
# ----------------------------------------------------------------------
class HROldPasswordReset(BaseModel):
    old_password: str
    new_password: str
    confirm_password: str

@router.put("/reset-password/old")
def hr_reset_password_old(
    payload: HROldPasswordReset,
    db: Session = Depends(get_db),
    employee_id: str = Depends(hr_session_required),   # HR or SUPERADMIN token
):
    ud = db.query(UserDetail).filter(UserDetail.employee_id == employee_id).first()
    if not ud:
        raise HTTPException(404, "HR_NOT_FOUND")

    pi = (
        db.query(ProfileInformation)
        .filter(ProfileInformation.employee_id == employee_id)
        .first()
    )
    if not pi:
        raise HTTPException(404, "PROFILE_NOT_FOUND")

    role = (
        db.query(UserRole)
        .filter(UserRole.role_id == pi.role_id)
        .first()
    )
    if not role:
        raise HTTPException(400, "ROLE_NOT_FOUND")

    if role.role_name.upper() != "HR":
        raise HTTPException(403, "ONLY_HR_CAN_CHANGE_OWN_PASSWORD")

    if not verify_password(payload.old_password, ud.password):
        raise HTTPException(400, "OLD_PASSWORD_WRONG")

    if payload.new_password != payload.confirm_password:
        raise HTTPException(400, "PASSWORD_MISMATCH")

    ud.password = get_password_hash(payload.new_password)
    db.commit()

    return {"message": "Password changed successfully"}

