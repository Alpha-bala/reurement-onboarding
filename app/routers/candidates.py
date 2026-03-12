import os
import re
import pyotp
from datetime import datetime, timedelta
from typing import Dict

from fastapi import (
    APIRouter, HTTPException, Depends, BackgroundTasks, Request, Query
)
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session, joinedload
from app.database import get_db

# ====== Models / Schemas / Utils ======
from app.models.user_models import (
    MFASecret,
    OTPRequest,
    CandidateProfileInformation,
    CandidateDocument,
    UserSession,
)
from app.utils.aws_email import aws_send_mail
from app.utils.notify_depends import send_notification_to_employee
from app.models.auth_models import ProfileInformation, UserRole
from app.schemas.user_schemas import (
    CandidateRegister,
    ResetPasswordIn,
    OTPSendOut,
    OTPVerifyIn,
    OTPVerifyOut,
    NotificationStructure,
    LoginRequest, OTPPurpose
)
from app.utils.helper import (
    verify_password,
    create_access_token,
    get_password_hash,
    require_candidate_only
)
from app.utils.session_utils import (
    create_user_session,
    logout_user_session
)
from app.utils.otp import (
    _generate_otp,
    _send_email_otp,
    OTP_EXP_MINUTES,
    RESEND_COOLDOWN_SECONDS,
    _active_otp_request,
    _seconds_until,
)
from app.utils.guards import candidate_session_required

# ===============================================================
# Config
# ===============================================================
router = APIRouter(prefix="/candidates", tags=["Candidates"])

# Product-level MFA toggle (for whitelabel)
MFA_FEATURE_ENABLED = os.getenv("MFA_FEATURE_ENABLED", "true").lower() == "true"

# MFA temp ticket expiry (in minutes) for login OTP step
TEMP_TICKET_EXPIRE_MINUTES = int(os.getenv("TEMP_TICKET_EXPIRE_MINUTES", "5"))

OTP_FRESH_MIN = int(os.getenv("OTP_FRESH_MIN", "15"))


# ===============================================================
# Password & Helper Utilities
# ===============================================================

def _hash_password_strict(password: str) -> str:
    if not isinstance(password, str) or not password.strip():
        raise HTTPException(status_code=400, detail="Invalid password")

    validate_password_rules(password)

    try:
        return get_password_hash(password)   # argon2 or bcrypt (both valid)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Password hashing failed"
        )

def generate_candidate_id(db: Session) -> str:
    """Generate sequential candidate IDs like CAND0001, CAND0002."""
    last = db.query(CandidateProfileInformation).order_by(
        CandidateProfileInformation.candidate_id.desc()
    ).first()
    if not last or not (last.candidate_id or "").startswith("CAND"):
        return "CAND0001"
    try:
        num = int((last.candidate_id or "")[4:]) + 1
    except Exception:
        ids = [
            int((cid[4:]))
            for (cid,) in db.query(CandidateProfileInformation.candidate_id).all()
            if cid and cid.startswith("CAND") and cid[4:].isdigit()
        ]
        num = max(ids, default=0) + 1
    return f"CAND{num:04d}"


def validate_password_rules(password: str):
    """Strong password enforcement."""
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(status_code=400, detail="Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise HTTPException(status_code=400, detail="Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Password must contain at least one digit")
    if not re.search(r"[!@#$%^&*]", password):
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least one special character (!@#$%^&*)",
        )

# ======================================================
# otp helpers
# ======================================================

import hashlib

def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()

def verify_otp(plain: str, hashed: str) -> bool:
    return hash_otp(plain) == hashed

# ===============================================================
# Registration Notifications
# ===============================================================
def _notify_hr_new_candidate_sync(employee_id: str, payload: NotificationStructure):
    """
    Run async notification safely from sync background task
    WITHOUT changing original notification function.
    """
    import asyncio
    from app.database import db_session

    async def runner():
        with db_session() as db:
            await send_notification_to_employee(
                employee_id=employee_id,
                notification_data=payload,
                db=db
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(runner())
    except RuntimeError:
        asyncio.run(runner())

# ===============================================================
# Candidate Registration
# ===============================================================
@router.post("/register")
def register_candidate(
        data: CandidateRegister,
        db: Session = Depends(get_db),
        background_tasks: BackgroundTasks = None,
):
    """Register new candidate after OTP verification."""
    if db.query(CandidateProfileInformation).filter(
            CandidateProfileInformation.email == data.email
    ).first():
        raise HTTPException(status_code=409, detail="EMAIL_EXISTS")

    verified = db.query(OTPRequest).filter(
        OTPRequest.email == data.email,
        OTPRequest.purpose == "signup",
        OTPRequest.verified_at.isnot(None),
        OTPRequest.verified_at >= (datetime.utcnow() - timedelta(minutes=OTP_FRESH_MIN)),
    ).first()
    if not verified:
        raise HTTPException(status_code=403, detail="OTP_VERIFICATION_REQUIRED")

    validate_password_rules(data.password)
    cand_id = generate_candidate_id(db)

    cand = CandidateProfileInformation(
        candidate_id=cand_id,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        contact_number=data.contact_number,
        highest_qualification=data.highest_qualification,
        total_experience=data.total_experience,
        relevant_experience=data.relevant_experience,
        skills=data.skills.dict() if data.skills else None,
        password=_hash_password_strict(data.password),
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    # Notification object (NOT dict)
    notif = NotificationStructure(
        scenario="candidate_registered",
        message=f"New candidate registered: {cand.first_name} {cand.last_name}",
        data={
            "candidate_id": cand_id,
            "first_name": cand.first_name,
            "last_name": cand.last_name,
            "email": cand.email,
            "contact_number": cand.contact_number,
        },
    )

    payload = notif  # 🔥 IMPORTANT FIX

    hr_users = (
        db.query(ProfileInformation)
        .options(joinedload(ProfileInformation.role))
        .join(UserRole, ProfileInformation.role_id == UserRole.role_id)
        .filter(UserRole.role_name.in_(["SUPERADMIN"]))
        .all()
    )

    for hr in hr_users:
        if background_tasks:
            background_tasks.add_task(
                _notify_hr_new_candidate_sync,
                hr.employee_id,
                payload
            )
        else:
            _notify_hr_new_candidate_sync(
                hr.employee_id,
                payload
            )

    return {
        "message": "Candidate created. Registration completed.",
        "candidate_id": cand_id
    }



# ===============================================================
# OTP (Send / Verify / Resend) — FINAL UPDATED WITH "forgot"
# ===============================================================

@router.post("/send", response_model=OTPSendOut)
async def send_otp(
        email: EmailStr = Query(..., description="Candidate email"),
        purpose: OTPPurpose = Query(..., description="Select purpose: signup / reset / forgot"),
        db: Session = Depends(get_db),
):
    """
    Send OTP using query parameters.
    Supports: signup, reset, forgot.
    """

    purpose_value = purpose.value  # ("signup" | "reset" | "forgot")

    # signup → must NOT exist
    if purpose_value == "signup":
        if db.query(CandidateProfileInformation).filter(
                CandidateProfileInformation.email == email
        ).first():
            raise HTTPException(status_code=409, detail="EMAIL_EXISTS")

    # reset / forgot → must exist
    if purpose_value in {"reset", "forgot"}:
        if not db.query(CandidateProfileInformation).filter(
                CandidateProfileInformation.email == email
        ).first():
            raise HTTPException(status_code=404, detail="Email not registered")

    existing = _active_otp_request(db, email=email, purpose=purpose_value)
    now = datetime.utcnow()

    otp = _generate_otp()

    if existing:
        # Cooldown check
        if now < (
                (existing.expires_at - timedelta(minutes=OTP_EXP_MINUTES))
                + timedelta(seconds=RESEND_COOLDOWN_SECONDS)
        ):
            return OTPSendOut(
                otp_request_id=existing.otp_id,
                expires_at=existing.expires_at,
                resend_in_seconds=_seconds_until(existing.expires_at),
            )

        # Update existing OTP
        existing.otp_hash = hash_otp(otp)
        existing.attempts = 0
        existing.expires_at = now + timedelta(minutes=OTP_EXP_MINUTES)

        db.commit()
        db.refresh(existing)

        await _send_email_otp(email, otp, purpose=purpose_value)

        return OTPSendOut(
            otp_request_id=existing.otp_id,
            expires_at=existing.expires_at,
            resend_in_seconds=RESEND_COOLDOWN_SECONDS,
        )

    new = OTPRequest(
        email=email,
        purpose=purpose_value,
        role="candidate",
        otp_hash=hash_otp(otp),
        attempts=0,
        max_attempts=5,
        expires_at=now + timedelta(minutes=OTP_EXP_MINUTES),
    )

    db.add(new)
    db.commit()
    db.refresh(new)

    await _send_email_otp(email, otp, purpose=purpose_value)

    return OTPSendOut(
        otp_request_id=new.otp_id,
        expires_at=new.expires_at,
        resend_in_seconds=RESEND_COOLDOWN_SECONDS,
    )

@router.post("/verify", response_model=OTPVerifyOut)
def email_verify_otp(payload: OTPVerifyIn, db: Session = Depends(get_db)):
    """Verify OTP correctness."""
    req = db.query(OTPRequest).filter(OTPRequest.otp_id == payload.otp_request_id).first()
    if not req or req.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="No valid OTP. Please request a new one.")
    if not verify_otp(payload.otp, req.otp_hash):
        req.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid OTP")
    req.verified_at = datetime.utcnow()
    db.commit()
    db.refresh(req)
    return OTPVerifyOut(
        verified=True,
        email=req.email,
        purpose=req.purpose,
        verified_at=req.verified_at,
    )

# @router.post("/resend", response_model=OTPSendOut)
# async def resend_otp(
#     email: EmailStr = Query(...),
#     purpose: OTPPurpose = Query(...),
#     db: Session = Depends(get_db)
# ):
#     """Resend OTP for signup, reset, or forgot purpose."""
#     return await send_otp(email=email, purpose=purpose, db=db)

# ===============================================================
# Candidate Login (Final Mandatory/Optional MFA Logic)
# ===============================================================
@router.post("/login", summary="Candidate login (secure MFA flow)")
def candidate_login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    password = payload.password or ""
    user_selected_mfa = bool(payload.mfa_enabled)  # Checkbox on login screen

    candidate = (
        db.query(CandidateProfileInformation)
        .filter(CandidateProfileInformation.email == email, CandidateProfileInformation.is_active==1)
        .first())

    if not candidate or not verify_password(password, candidate.password or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    candidate_id = str(candidate.candidate_id)

    mfa = db.query(MFASecret).filter(
        MFASecret.user_id == candidate_id,
        MFASecret.role_type == "CANDIDATE"
    ).first()

    # =============================================================
    # RULE #1 — Fully verified MFA exists → ALWAYS REQUIRE MFA
    # =============================================================
    if mfa and mfa.is_verified and mfa.mfa_enabled and mfa.secret_key:
        ticket = create_access_token(
            data={
                "sub": candidate_id,
                "role": "CANDIDATE",
                "mfa_required": True,
                "token_use": "mfa_ticket",
            },
            expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES)
        )

        return {
            "status": "MFA_REQUIRED",
            "ticket": ticket,
            "role": "CANDIDATE",
            "user_id": candidate_id,
            "must_enroll": False
        }

    # =============================================================
    # RULE #2 — User chooses MFA → Start or Continue MFA Enrollment
    # =============================================================
    if user_selected_mfa:

        # No existing MFA → create a fresh enrollment
        if not mfa:
            mfa = MFASecret(
                user_id=candidate_id,
                role_type="CANDIDATE",
                secret_key=pyotp.random_base32(),
                is_verified=False,
                mfa_enabled=False,
                backup_codes=[]
            )
            db.add(mfa)
            db.commit()
            db.refresh(mfa)

        # MFA exists but incomplete → reset for clean enrollment
        elif not mfa.secret_key or not mfa.is_verified:
            mfa.secret_key = pyotp.random_base32()
            mfa.is_verified = False
            mfa.mfa_enabled = False
            mfa.backup_codes = []
            db.commit()

        # Enrollment ticket
        ticket = create_access_token(
            data={
                "sub": candidate_id,
                "role": "CANDIDATE",
                "mfa_required": True,
                "token_use": "mfa_ticket",
            },
            expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES)
        )

        return {
            "status": "SETUP_MFA",
            "ticket": ticket,
            "role": "CANDIDATE",
            "user_id": candidate_id,
            "must_enroll": True
        }

    # =============================================================
    # RULE #3 — Normal Login (No MFA selected, No MFA enrolled)
    # =============================================================
    # CASES HANDLED HERE:
    #   ✔ No MFA record
    #   ✔ MFA record exists but NOT verified yet
    #   ✔ MFA record exists but NOT enabled
    #   ✔ User did not select MFA
    # All these SHOULD allow normal login.
    # =============================================================

    session = create_user_session(db, user_id=candidate_id, role_type="CANDIDATE")

    access_token = create_access_token({
        "sub": candidate_id,
        "role": "CANDIDATE",
        "mfa_verified": False,
        "session_id": session["session_id"],
    })

    db.query(UserSession).filter(
        UserSession.session_id == session["session_id"]
    ).update({"jwt_token": access_token})
    db.commit()

    return {
        "status": "LOGIN_SUCCESS",
        "message": "Login successful (MFA not enabled)",
        "token": access_token,
        "role": "CANDIDATE",
        "user_id": candidate_id,
        "session_id": session["session_id"]
    }

# ===============================================================
# Logout Candidate
# ===============================================================
@router.post("/logout", summary="Logout Candidate", status_code=200)
def candidate_logout(
        request: Request,
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    """
    Logs out the currently authenticated candidate.
        Invalidates their active session.
        Clears stored JWT from user_sessions table.
        Updates logout_time and sets is_active=False.
    """

    # --- Extract Bearer token from Authorization header ---
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
        )

    token = auth_header.split(" ")[1].strip()

    try:
        # --- Perform logout and session cleanup ---
        result = logout_user_session(db, token=token)

        # Build unified API response
        return {
            "status": "success",
            "message": "Candidate logged out successfully.",
            "user_id": result["user_id"],
            "role": result["role"],
            "session_id": result["session_id"],
            "logout_time": result["logout_time"],
        }

    except HTTPException as e:
        # Pass through expected session or token errors
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        # Handle unexpected issues gracefully
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected logout error: {str(e)}",
        )


# ===============================================================
# Reset Password
# ===============================================================
@router.post("/forgot-password")
def forgot_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    """Reset candidate password after verified OTP."""
    cand = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.email == payload.email
    ).first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    validate_password_rules(payload.password)

    verified = db.query(OTPRequest).filter(
        OTPRequest.email == payload.email,
        OTPRequest.purpose == "forgot",
        OTPRequest.verified_at.isnot(None),
        OTPRequest.verified_at >= (datetime.utcnow() - timedelta(minutes=OTP_FRESH_MIN)),
    ).first()
    if not verified:
        raise HTTPException(status_code=400, detail="You must verify OTP before resetting password")

    cand.password = _hash_password_strict(payload.password)
    db.commit()
    return {"message": "Password reset successfully"}


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str
    confirm_new_password: str

@router.post("/reset-password")
async def change_password(
    payload: ChangePasswordIn,
    db: Session = Depends(get_db),
    candidate_id: str = Depends(candidate_session_required),
):
    """
    Change password using old password (logged-in candidate)
    and send confirmation email.
    """

    candidate = (
        db.query(CandidateProfileInformation)
        .filter(CandidateProfileInformation.candidate_id == candidate_id)
        .first()
    )

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not verify_password(payload.old_password, candidate.password):
        raise HTTPException(
            status_code=400,
            detail="Old password is incorrect"
        )

    if verify_password(payload.new_password, candidate.password):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from old password"
        )

    if payload.new_password != payload.confirm_new_password:
        raise HTTPException(
            status_code=400,
            detail="New passwords do not match"
        )

    validate_password_rules(payload.new_password)

    candidate.password = _hash_password_strict(payload.new_password)
    db.commit()

    email_subject = "Your password has been changed successfully"

    email_body = f"""
Hi {candidate.first_name},

This is a confirmation that your account password was changed successfully.

If you made this change, no further action is required.

If you did NOT change your password, please contact our support team immediately.

For security reasons, we recommend logging in again.

Regards,
SecurXperts Team
"""

    await aws_send_mail(
        reciver_to=[candidate.email],
        subject=email_subject,
        body=email_body,
        body_type="plain"
    )

    return {
        "message": "Password changed successfully. Please login again."
    }

# ===============================================================
# 👤 Authenticated Candidate Profile
# ===============================================================
@router.get(
    "/me/full",
    dependencies=[Depends(require_candidate_only), Depends(candidate_session_required)],
)
def get_my_full_profile(
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    """Fetch authenticated candidate’s complete profile."""
    profile = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Candidate not found")

    docs = (
        db.query(CandidateDocument)
        .filter(CandidateDocument.candidate_id == candidate_id)
        .order_by(CandidateDocument.uploaded_at.desc())
        .all()
    )

    return {
        "candidate_id": profile.candidate_id,
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "email": profile.email,
        "contact_number": profile.contact_number,
        "date_of_birth": profile.date_of_birth,
        "gender": profile.gender,
        "address": profile.address,
        "highest_qualification": profile.highest_qualification,
        "education": profile.education,
        "skills": profile.skills,
        "total_experience": profile.total_experience,
        "relevant_experience": profile.relevant_experience,
        "experience": profile.experience,
        "documents": [
            {
                "doc_id": d.doc_id,
                "doc_name": d.doc_name,
                "doc_sub_name": d.doc_sub_name,
                "file_name": d.file_name,
                "file_path": d.file_path,
                "uploaded_at": d.uploaded_at,
            }
            for d in docs
        ],
    }
