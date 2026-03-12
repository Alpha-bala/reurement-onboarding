from datetime import timedelta
import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth_models import UserDetail, ProfileInformation, UserRole
from app.models.user_models import MFASecret, UserSession
from app.utils.helper import verify_password, create_access_token
from app.utils.session_utils import create_user_session, logout_user_session

router = APIRouter(prefix="/auth/hr", tags=["auth"])
TEMP_TICKET_EXPIRE_MINUTES = 5

# ===============================================================
# Request Schema
# ===============================================================
class HRLoginRequest(BaseModel):
    email: EmailStr
    password: str
    mfa_enabled: bool

# ===============================================================
# HR Login (Secure MFA Flow — same logic as Candidate login)
# ===============================================================
@router.post("/login", summary="HR login (secure MFA flow)")
def hr_login(payload: HRLoginRequest, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    password = payload.password or ""
    user_selected_mfa = bool(payload.mfa_enabled)

    ud = (
        db.query(UserDetail)
        .filter(UserDetail.user_name == email, UserDetail.is_active == 1)
        .first()
    )
    if not ud or not verify_password(password, ud.password or ""):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    pi = (
        db.query(ProfileInformation)
        .filter(
            ProfileInformation.employee_id == ud.employee_id
        )
        .first()
    )
    if not pi:
        raise HTTPException(status_code=403, detail="Profile inactive or missing")

    role_name = (
            db.query(UserRole.role_name)
            .filter(UserRole.role_id == pi.role_id)
            .scalar() or ""
    ).upper()

    if role_name != "HR":
        raise HTTPException(status_code=403, detail="Only HR can log in here")

    hr_id = str(ud.employee_id)

    mfa = db.query(MFASecret).filter(
        MFASecret.user_id == hr_id,
        MFASecret.role_type == "HR"
    ).first()

    # =============================================================
    # RULE #1 — Fully verified MFA exists → ALWAYS REQUIRE MFA
    # =============================================================
    if (
            mfa
            and mfa.is_verified
            and mfa.mfa_enabled
            and mfa.secret_key
    ):
        ticket = create_access_token(
            data={
                "sub": hr_id,
                "role": "HR",
                "mfa_required": True,
                "token_use": "mfa_ticket"
            },
            expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES)
        )

        return {
            "status": "MFA_REQUIRED",
            "ticket": ticket,
            "role": "HR",
            "user_id": hr_id,
            "must_enroll": False
        }

    # =============================================================
    # RULE #2 — HR chooses MFA → Start / Continue Enrollment
    # =============================================================
    if user_selected_mfa:

        # No MFA record → create one
        if not mfa:
            mfa = MFASecret(
                user_id=hr_id,
                role_type="HR",
                secret_key=pyotp.random_base32(),
                is_verified=False,
                mfa_enabled=False,
                backup_codes=[]
            )
            db.add(mfa)
            db.commit()
            db.refresh(mfa)

        # Incomplete MFA → reset safely
        elif not mfa.secret_key or not mfa.is_verified:
            mfa.secret_key = pyotp.random_base32()
            mfa.is_verified = False
            mfa.mfa_enabled = False
            mfa.backup_codes = []
            db.commit()

        # Issue MFA enrollment ticket
        ticket = create_access_token(
            data={
                "sub": hr_id,
                "role": "HR",
                "mfa_required": True,
                "token_use": "mfa_ticket"
            },
            expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES)
        )

        return {
            "status": "SETUP_MFA",
            "ticket": ticket,
            "role": "HR",
            "user_id": hr_id,
            "must_enroll": True
        }

    # =============================================================
    # RULE #3 — Normal Login (No MFA selected, No MFA enrolled)
    # =============================================================
    # Covers:
    #   ✔ No MFA record
    #   ✔ MFA exists but NOT verified
    #   ✔ MFA exists but NOT enabled
    #   ✔ HR did NOT select MFA
    # =============================================================

    session = create_user_session(db, user_id=hr_id, role_type="HR")
    access_token = create_access_token({
        "sub": hr_id,
        "role": "HR",
        "mfa_verified": False,
        "session_id": session["session_id"],
        "kind": "access"
    })

    db.query(UserSession).filter(
        UserSession.session_id == session["session_id"]
    ).update({"jwt_token": access_token})
    db.commit()

    return {
        "status": "LOGIN_SUCCESS",
        "message": "Login successful (MFA not enabled)",
        "token": access_token,
        "role": "HR",
        "user_id": hr_id,
        "session_id": session["session_id"],
        "expires_at": session["expires_at"],
        "login_time": session["login_time"]
    }

# =======================================================================
# HR Logout
# =======================================================================
@router.post("/logout", summary="Logout HR", status_code=200)
def hr_logout(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    token = auth_header.split(" ")[1].strip()

    try:
        result = logout_user_session(db, token=token)
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected logout error: {str(e)}",
        )

    return {
        "status": "success",
        "role": result["role"],
        "user_id": result["user_id"],
        "session_id": result["session_id"],
        "logout_time": result["logout_time"],
        "message": "HR logged out successfully.",
    }

