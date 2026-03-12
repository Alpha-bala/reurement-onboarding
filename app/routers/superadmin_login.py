# import pyotp
# from datetime import timedelta
# from fastapi import APIRouter, Depends, HTTPException, Request, status
# from pydantic import BaseModel, EmailStr
# from sqlalchemy.orm import Session

# from app.database import get_db
# from app.models.auth_models import UserDetail, ProfileInformation, UserRole
# from app.models.user_models import MFASecret, UserSession
# from app.utils.helper import verify_password, create_access_token
# from app.utils.session_utils import create_user_session, logout_user_session

# router = APIRouter(prefix="/auth/superadmin", tags=["auth"])

# TEMP_TICKET_EXPIRE_MINUTES = 5


# # ==============================================================================
# # REQUEST SCHEMA
# # ==============================================================================
# class SuperAdminLoginRequest(BaseModel):
#     email: EmailStr
#     password: str
#     mfa_enabled: bool


# # ==============================================================================
# # Superadmin Login (Secure MFA Flow)
# # ==============================================================================
# @router.post("/login", summary="Superadmin login (secure MFA flow)")
# def superadmin_login(
#     payload: SuperAdminLoginRequest,
#     db: Session = Depends(get_db),
# ):
#     email = payload.email.strip().lower()
#     password = payload.password or ""
#     user_selected_mfa = bool(payload.mfa_enabled)

#     # --------------------------------------------------
#     # Validate credentials
#     # --------------------------------------------------
#     ud = (
#         db.query(UserDetail)
#         .filter(
#             UserDetail.user_name == email,
#             UserDetail.is_active == 1
#         )
#         .first()
#     )

#     if not ud or not verify_password(password, ud.password or ""):
#         raise HTTPException(status_code=401, detail="Invalid username or password")

#     # --------------------------------------------------
#     # Validate profile & role
#     # --------------------------------------------------
#     pi = (
#         db.query(ProfileInformation)
#         .filter(ProfileInformation.employee_id == ud.employee_id)
#         .first()
#     )
#     if not pi:
#         raise HTTPException(status_code=403, detail="Profile inactive or missing")

#     role_name = (
#         db.query(UserRole.role_name)
#         .filter(UserRole.role_id == pi.role_id)
#         .scalar()
#         or ""
#     ).upper()

#     if role_name != "SUPERADMIN":
#         raise HTTPException(status_code=403, detail="Only SUPERADMIN can log in here")

#     sa_id = str(ud.employee_id)

#     # --------------------------------------------------
#     # Fetch MFA record (if any)
#     # --------------------------------------------------
#     mfa = (
#         db.query(MFASecret)
#         .filter(
#             MFASecret.user_id == sa_id,
#             MFASecret.role_type == "SUPERADMIN"
#         )
#         .first()
#     )

#     # ==================================================
#     # RULE #1 — MFA already verified → ALWAYS REQUIRE MFA
#     # ==================================================
#     if (
#         mfa
#         and mfa.is_verified
#         and mfa.mfa_enabled
#         and mfa.secret_key
#     ):
#         ticket = create_access_token(
#             data={
#                 "sub": sa_id,
#                 "role": "SUPERADMIN",
#                 "mfa_required": True,
#                 "token_use": "mfa_ticket",
#             },
#             expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES),
#         )

#         return {
#             "status": "MFA_REQUIRED",
#             "ticket": ticket,
#             "role": "SUPERADMIN",
#             "user_id": sa_id,
#             "must_enroll": False,
#         }

#     # ==================================================
#     # RULE #2 — User chooses MFA → Start / Continue Setup
#     # ==================================================
#     if user_selected_mfa:

#         if not mfa:
#             mfa = MFASecret(
#                 user_id=sa_id,
#                 role_type="SUPERADMIN",
#                 secret_key=pyotp.random_base32(),
#                 is_verified=False,
#                 mfa_enabled=False,
#                 backup_codes=[],
#             )
#             db.add(mfa)

#         else:
#             mfa.secret_key = pyotp.random_base32()
#             mfa.is_verified = False
#             mfa.mfa_enabled = False
#             mfa.backup_codes = []

#         db.commit()
#         db.refresh(mfa)

#         ticket = create_access_token(
#             data={
#                 "sub": sa_id,
#                 "role": "SUPERADMIN",
#                 "mfa_required": True,
#                 "token_use": "mfa_ticket",
#             },
#             expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES),
#         )

#         return {
#             "status": "SETUP_MFA",
#             "ticket": ticket,
#             "role": "SUPERADMIN",
#             "user_id": sa_id,
#             "must_enroll": True,
#         }

#     # ==================================================
#     # RULE #3 — Normal Login (No MFA)
#     # ==================================================
#     session = create_user_session(
#         db,
#         user_id=sa_id,
#         role_type="SUPERADMIN",
#     )

#     access_token = create_access_token(
#         {
#             "sub": sa_id,
#             "role": "SUPERADMIN",
#             "mfa_verified": False,
#             "session_id": session["session_id"],
#             "kind": "access",
#         }
#     )

#     db.query(UserSession).filter(
#         UserSession.session_id == session["session_id"]
#     ).update({"jwt_token": access_token})

#     db.commit()

#     return {
#         "status": "LOGIN_SUCCESS",
#         "message": "Login successful (MFA not enabled)",
#         "token": access_token,
#         "role": "SUPERADMIN",
#         "user_id": sa_id,
#         "session_id": session["session_id"],
#         "expires_at": session["expires_at"],
#         "login_time": session["login_time"],
#     }


# # ==============================================================================
# # Superadmin Logout
# # ==============================================================================
# @router.post("/logout", summary="Logout Superadmin", status_code=200)
# def superadmin_logout(
#     request: Request,
#     db: Session = Depends(get_db),
# ):
#     auth_header = request.headers.get("authorization")

#     if not auth_header or not auth_header.lower().startswith("bearer "):
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Missing or invalid Authorization header",
#         )

#     token = auth_header.split(" ", 1)[1].strip()

#     try:
#         result = logout_user_session(db, token=token)
#     except HTTPException as e:
#         raise HTTPException(status_code=e.status_code, detail=e.detail)
#     except Exception:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Unexpected logout error",
#         )

#     return {
#         "status": "success",
#         "role": result["role"],
#         "user_id": result["user_id"],
#         "session_id": result["session_id"],
#         "logout_time": result["logout_time"],
#         "message": "Superadmin logged out successfully",
#     }


import os
import pyotp
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth_models import UserDetail, ProfileInformation, UserRole
from app.models.user_models import MFASecret, UserSession
from app.schemas.user_admin_schemas import SuperAdminCreateRequest
from app.utils.helper import verify_password, create_access_token, get_password_hash
from app.utils.session_utils import create_user_session, logout_user_session
from dotenv import load_dotenv
load_dotenv()

router = APIRouter(prefix="/auth/superadmin", tags=["auth"])

TEMP_TICKET_EXPIRE_MINUTES = 5

# ==============================================================================
# LICENSE CONFIG (FROM .ENV)
# ==============================================================================
SUPERADMIN_LICENSE_KEY = os.getenv("SUPERADMIN_LICENSE_KEY")
SUPERADMIN_LIMIT = int(os.getenv("SUPERADMIN_LIMIT", "0"))


# ==============================================================================
# INTERNAL HELPER
# ==============================================================================
def get_superadmin_count(db: Session) -> int:
    return (
        db.query(ProfileInformation)
        .join(UserRole, ProfileInformation.role_id == UserRole.role_id)
        .filter(UserRole.role_name == "SUPERADMIN")
        .count()
    )


# ==============================================================================
# REQUEST SCHEMAS
# ==============================================================================

class SuperAdminLoginRequest(BaseModel):
    email: EmailStr
    password: str
    mfa_enabled: bool


# ==============================================================================
# CREATE SUPERADMIN (LICENSE + LIMIT BASED)
# ==============================================================================
@router.post(
    "/create",
    summary="Create Superadmin using license key",
    status_code=status.HTTP_201_CREATED,
)
def create_superadmin(
    license_key: str,
    payload: SuperAdminCreateRequest,
    db: Session = Depends(get_db),
):
    # 1️⃣ Validate license key
    if not SUPERADMIN_LICENSE_KEY or license_key != SUPERADMIN_LICENSE_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INVALID_LICENSE_KEY",
        )

    # 2️⃣ Enforce limit
    current_count = get_superadmin_count(db)
    if current_count >= SUPERADMIN_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SUPERADMIN_LIMIT_REACHED",
        )

    # 3️⃣ Fetch SUPERADMIN role
    role = db.query(UserRole).filter(
        UserRole.role_name == "SUPERADMIN"
    ).first()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPERADMIN_ROLE_NOT_FOUND",
        )

    # 4️⃣ Prevent duplicate user
    if db.query(UserDetail).filter(
        (UserDetail.employee_id == payload.employee_id) |
        (UserDetail.user_name == payload.user_name)
    ).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="USER_ALREADY_EXISTS",
        )

    # 5️⃣ Create UserDetail
    user = UserDetail(
        employee_id=payload.employee_id,
        user_name=payload.user_name,
        password=get_password_hash(payload.password),
        is_active=True,
    )

    # 6️⃣ Create ProfileInformation
    profile = ProfileInformation(
        employee_id=payload.employee_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        role_id=role.role_id,
    )

    db.add_all([user, profile])
    db.commit()

    return {
        "message": "SUPERADMIN_CREATED_SUCCESSFULLY",
        "current_superadmins": current_count + 1,
        "remaining_slots": SUPERADMIN_LIMIT - (current_count + 1),
    }


# ==============================================================================
# SUPERADMIN LOGIN (UNCHANGED)
# ==============================================================================
@router.post("/login", summary="Superadmin login (secure MFA flow)")
def superadmin_login(
    payload: SuperAdminLoginRequest,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    password = payload.password or ""
    user_selected_mfa = bool(payload.mfa_enabled)

    ud = (
        db.query(UserDetail)
        .filter(
            UserDetail.user_name == email,
            UserDetail.is_active == 1
        )
        .first()
    )

    if not ud or not verify_password(password, ud.password or ""):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    pi = (
        db.query(ProfileInformation)
        .filter(ProfileInformation.employee_id == ud.employee_id)
        .first()
    )
    if not pi:
        raise HTTPException(status_code=403, detail="Profile inactive or missing")

    role_name = (
        db.query(UserRole.role_name)
        .filter(UserRole.role_id == pi.role_id)
        .scalar()
        or ""
    ).upper()

    if role_name != "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Only SUPERADMIN can log in here")

    sa_id = str(ud.employee_id)

    mfa = (
        db.query(MFASecret)
        .filter(
            MFASecret.user_id == sa_id,
            MFASecret.role_type == "SUPERADMIN"
        )
        .first()
    )

    if mfa and mfa.is_verified and mfa.mfa_enabled and mfa.secret_key:
        ticket = create_access_token(
            data={
                "sub": sa_id,
                "role": "SUPERADMIN",
                "token_use": "mfa_ticket",
            },
            expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES),
        )
        return {
            "status": "MFA_REQUIRED",
            "ticket": ticket,
            "role": "SUPERADMIN",
            "user_id": sa_id,
            "must_enroll": False,
        }

    if user_selected_mfa:
        if not mfa:
            mfa = MFASecret(
                user_id=sa_id,
                role_type="SUPERADMIN",
                secret_key=pyotp.random_base32(),
                is_verified=False,
                mfa_enabled=False,
                backup_codes=[],
            )
            db.add(mfa)
        else:
            mfa.secret_key = pyotp.random_base32()
            mfa.is_verified = False
            mfa.mfa_enabled = False
            mfa.backup_codes = []

        db.commit()
        db.refresh(mfa)

        ticket = create_access_token(
            data={
                "sub": sa_id,
                "role": "SUPERADMIN",
                "token_use": "mfa_ticket",
            },
            expires_delta=timedelta(minutes=TEMP_TICKET_EXPIRE_MINUTES),
        )
        return {
            "status": "SETUP_MFA",
            "ticket": ticket,
            "role": "SUPERADMIN",
            "user_id": sa_id,
            "must_enroll": True,
        }

    session = create_user_session(db, user_id=sa_id, role_type="SUPERADMIN")

    access_token = create_access_token(
        {
            "sub": sa_id,
            "role": "SUPERADMIN",
            "session_id": session["session_id"],
        }
    )

    db.query(UserSession).filter(
        UserSession.session_id == session["session_id"]
    ).update({"jwt_token": access_token})

    db.commit()

    return {
        "status": "LOGIN_SUCCESS",
        "token": access_token,
        "role": "SUPERADMIN",
        "user_id": sa_id,
        "session_id": session["session_id"],
        "expires_at": session["expires_at"],
        "login_time": session["login_time"],
    }


# ==============================================================================
# SUPERADMIN LOGOUT (UNCHANGED)
# ==============================================================================
@router.post("/logout", summary="Logout Superadmin", status_code=200)
def superadmin_logout(
    request: Request,
    db: Session = Depends(get_db),
):
    auth_header = request.headers.get("authorization")

    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    token = auth_header.split(" ", 1)[1].strip()

    result = logout_user_session(db, token=token)

    return {
        "status": "success",
        "role": result["role"],
        "user_id": result["user_id"],
        "session_id": result["session_id"],
        "logout_time": result["logout_time"],
        "message": "Superadmin logged out successfully",
    }
