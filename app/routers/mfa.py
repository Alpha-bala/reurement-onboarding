from __future__ import annotations
import os
import io
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, constr

from app.database import get_db
from app.models.user_models import MFASecret, UserSession
from app.schemas.user_schemas import MFAVerifyIn
from app.utils.helper import create_access_token, decode_access_token
from app.utils.session_utils import create_user_session

# ===============================================================
# CONSTANTS
# ===============================================================
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


router = APIRouter(prefix="/mfa", tags=["MFA"])

_ALLOWED_ROLES = {"CANDIDATE", "HR", "SUPERADMIN"}
_APP_NAME = "recruitment"


# ===============================================================
#  MODEL: LOGIN VERIFY INPUT
# ===============================================================
from pydantic import BaseModel, StringConstraints
from typing_extensions import Annotated

class LoginVerifyIn(BaseModel):
    temp_token: str
    code: Annotated[str,StringConstraints(strip_whitespace=True,min_length=4,max_length=32)]

# ===============================================================
#  UTILS
# ===============================================================
def _require_temp_ticket(token: str) -> dict:
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired MFA ticket")

    if payload.get("token_use") != "mfa_ticket":
        raise HTTPException(401, "Not a valid MFA ticket")

    user_id = payload.get("sub")
    role = (payload.get("role") or "").upper()

    if not user_id or role not in _ALLOWED_ROLES:
        raise HTTPException(401, "Invalid MFA ticket payload")

    return {"user_id": str(user_id), "role": role}

def _get_mfa(db: Session, user_id: str, role: str):
    return db.query(MFASecret).filter(
        MFASecret.user_id == user_id,
        MFASecret.role_type == role
    ).first()

def _create_mfa(db: Session, user_id: str, role: str):
    mfa = MFASecret(
        user_id=user_id,
        role_type=role,
        secret_key=pyotp.random_base32(),
        is_verified=False,
        mfa_enabled=False,
        failed_attempts=0,
        locked_until=None,
        backup_codes=[]
    )
    db.add(mfa)
    db.commit()
    db.refresh(mfa)
    return mfa

# ===============================================================
#  START MFA ENROLLMENT
# ===============================================================
@router.post("/enroll/start")
def enroll_start(token: str = Query(...), db: Session = Depends(get_db)):
    claims = _require_temp_ticket(token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa:  # safe to create here
        mfa = _create_mfa(db, user_id, role)

    if mfa.is_verified:
        return {"status": "success", "message": "MFA already enabled."}

    label = f"{_APP_NAME}-{role}:{user_id}"
    issuer = f"{_APP_NAME}-{role}"
    uri = pyotp.TOTP(mfa.secret_key).provisioning_uri(name=label, issuer_name=issuer)

    return {
        "status": "success",
        "message": "Scan QR code or use otpauth URL",
        "data": {
            "user_id": user_id,
            "role": role,
            "otpauth_url": uri,
            "is_verified": False
        }
    }

# ===============================================================
# QR CODE FOR ENROLLMENT
# ===============================================================
@router.get("/enroll/qr", responses={200: {"content": {"image/png": {}}}})
def enroll_qr(token: str = Query(...), db: Session = Depends(get_db)):
    claims = _require_temp_ticket(token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa:
        raise HTTPException(404, "MFA not initialized")

    if mfa.is_verified:
        raise HTTPException(400, "MFA already verified")

    label = f"{_APP_NAME}-{role}:{user_id}"
    issuer = f"{_APP_NAME}-{role}"
    uri = pyotp.TOTP(mfa.secret_key).provisioning_uri(label, issuer)

    buf = io.BytesIO()
    qrcode.make(uri).save(buf, "PNG")
    buf.seek(0)

    return Response(content=buf.getvalue(), media_type="image/png")

# ===============================================================
# VERIFY ENROLLMENT
# ===============================================================
@router.post("/enroll/verify")
def enroll_verify(payload: MFAVerifyIn, token: str = Query(...), db: Session = Depends(get_db)):
    claims = _require_temp_ticket(token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa:
        raise HTTPException(404, "MFA not started")

    if not pyotp.TOTP(mfa.secret_key).verify(payload.code, valid_window=1):
        raise HTTPException(400, "Invalid OTP")

    mfa.is_verified = True
    mfa.mfa_enabled = True
    mfa.last_verified_at = now_ist()

    raw_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    mfa.backup_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]

    db.commit()

    return {
        "status": "success",
        "message": "MFA enabled successfully",
        "backup_codes": raw_codes
    }

# ===============================================================
# VERIFY LOGIN MFA
# ===============================================================
@router.post("/verify-login")
def verify_login(data: LoginVerifyIn, db: Session = Depends(get_db)):
    claims = _require_temp_ticket(data.temp_token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa or not mfa.mfa_enabled or not mfa.is_verified:
        raise HTTPException(400, "MFA not enabled for this user")

    totp_ok = pyotp.TOTP(mfa.secret_key).verify(data.code, valid_window=1)

    backup_ok = False
    if not totp_ok:
        hashed = hashlib.sha256(data.code.encode()).hexdigest()
        if hashed in mfa.backup_codes:
            mfa.backup_codes.remove(hashed)
            backup_ok = True

    if not (totp_ok or backup_ok):
        raise HTTPException(400, "Invalid MFA code")

    session = create_user_session(db, user_id=user_id, role_type=role)

    token = create_access_token({
        "sub": user_id,
        "role": role,
        "mfa_verified": True,
        "session_id": session["session_id"],
        "kind": "access"
    })

    db.query(UserSession).filter(
        UserSession.session_id == session["session_id"]
    ).update({"jwt_token": token})
    db.commit()

    return {
        "status": "success",
        "message": "Login successful with MFA",
        "data": {
            "access_token": token,
            "session_id": session["session_id"],
            "expires_at": session["expires_at"]
        }
    }

# ===============================================================
# ROTATE BACKUP CODES
# ===============================================================
@router.post("/backup/rotate")
def rotate_backup_codes(token: str = Query(...), db: Session = Depends(get_db)):
    payload = decode_access_token(token)
    user_id, role = payload.get("sub"), payload.get("role")

    mfa = _get_mfa(db, user_id, role)
    if not mfa:
        raise HTTPException(400, "MFA not enabled")

    raw_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    mfa.backup_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]
    mfa.last_rotated_at = now_ist()

    db.commit()

    return {"status": "success", "backup_codes": raw_codes}


# ===============================================================
# START RE-ENROLL USING BACKUP CODE
# ===============================================================
@router.post("/re-enroll/start")
def reenroll_start(
        backup_code: str = Query(...),
        temp_token: str = Query(...),
        db: Session = Depends(get_db)
):
    claims = _require_temp_ticket(temp_token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa:
        raise HTTPException(404, "MFA not enabled")

    hashed = hashlib.sha256(backup_code.encode()).hexdigest()

    if hashed not in mfa.backup_codes:
        raise HTTPException(400, "Invalid backup code")

    # Consume code
    mfa.backup_codes.remove(hashed)

    # Reset MFA
    mfa.secret_key = pyotp.random_base32()
    mfa.is_verified = False
    mfa.mfa_enabled = False

    db.commit()

    return {"status": "success", "message": "Backup code accepted. Re-enroll now."}


# ===============================================================
# RE-ENROLL QR CODE
# ===============================================================
@router.get("/re-enroll/qr", responses={200: {"content": {"image/png": {}}}})
def reenroll_qr(temp_token: str = Query(...), db: Session = Depends(get_db)):
    claims = _require_temp_ticket(temp_token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa or not mfa.secret_key:
        raise HTTPException(400, "MFA not ready")

    label = f"{_APP_NAME}-{role}:{user_id}"
    issuer = f"{_APP_NAME}-{role}"
    uri = pyotp.TOTP(mfa.secret_key).provisioning_uri(label, issuer)

    buf = io.BytesIO()
    qrcode.make(uri).save(buf, "PNG")
    buf.seek(0)

    return Response(content=buf.getvalue(), media_type="image/png")


# ===============================================================
# VERIFY RE-ENROLL
# ===============================================================
@router.post("/re-enroll/verify")
def reenroll_verify(payload: MFAVerifyIn, temp_token: str = Query(...), db: Session = Depends(get_db)):
    claims = _require_temp_ticket(temp_token)
    user_id, role = claims["user_id"], claims["role"]

    mfa = _get_mfa(db, user_id, role)
    if not mfa or not mfa.secret_key:
        raise HTTPException(400, "MFA not ready")

    if not pyotp.TOTP(mfa.secret_key).verify(payload.code, valid_window=1):
        raise HTTPException(400, "Invalid OTP")

    mfa.is_verified = True
    mfa.mfa_enabled = True
    mfa.last_verified_at = now_ist()

    db.commit()

    return {"status": "success", "message": "Re-enrollment successful"}


# ===============================================================
# DISABLE MFA — FINAL STABLE VERSION
# ===============================================================
@router.post("/disable")
def disable_mfa(
        code: str = Query(..., description="Enter TOTP or backup code"),
        token: str = Query(None, description="Access token (optional if using header)"),
        request: Request = None,
        db: Session = Depends(get_db)
):
    if not token:
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ")[1].strip()

    if not token:
        raise HTTPException(401, "Missing access token")

    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired access token")

    user_id = payload.get("sub")
    role = payload.get("role")

    if not user_id or not role:
        raise HTTPException(401, "Invalid token payload")

    mfa = _get_mfa(db, user_id, role)
    if not mfa:
        raise HTTPException(400, "MFA is not enabled for this user")

    # TOTP
    totp_ok = pyotp.TOTP(mfa.secret_key).verify(code, valid_window=1)

    backup_ok = False
    hashed = hashlib.sha256(code.encode()).hexdigest()

    if not totp_ok and hashed in mfa.backup_codes:
        mfa.backup_codes.remove(hashed)  # consume backup code
        backup_ok = True

    if not (totp_ok or backup_ok):
        raise HTTPException(400, "Invalid MFA code")

    db.delete(mfa)
    db.commit()

    return {
        "status": "success",
        "message": "MFA disabled successfully"
    }

