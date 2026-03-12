from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta
from typing import Optional

from app.database import get_db
from app.models.auth_models import UserDetail, ProfileInformation, UserRole
from app.models.user_models import OTPRequest
from app.schemas.user_admin_schemas import (
    UserCreate, UserDetailUpdate, ProfileUpdate,
    UserDetailOut, ProfileOut, UserWithProfileOut, PaginatedUserResponse
)
from app.utils.helper import get_password_hash
from app.utils.guards import superadmin_session_required, hr_session_required

OTP_FRESH_MIN = 15

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


# ==============================================================================
# INTERNAL HELPER — GET ROLE
# ==============================================================================
def _get_role(db: Session, employee_id: str) -> str:
    pi = (
        db.query(ProfileInformation)
        .options(joinedload(ProfileInformation.role))
        .filter(ProfileInformation.employee_id == employee_id)
        .first()
    )
    if not pi or not pi.role:
        raise HTTPException(404, "ROLE_NOT_FOUND")
    return pi.role.role_name.upper()


# ==============================================================================
# CREATE HR — OTP REQUIRED (SUPERADMIN BLOCKED)
# ==============================================================================
@router.post("", response_model=UserWithProfileOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _=Depends(superadmin_session_required),
):
    primary_email = (
        payload.profile.email.get("primary")
        if isinstance(payload.profile.email, dict)
        else payload.profile.email
    )

    if not primary_email:
        raise HTTPException(400, "PRIMARY_EMAIL_REQUIRED")

    # 🔐 OTP validation (HR only)
    otp = db.query(OTPRequest).filter(
        OTPRequest.email == primary_email,
        OTPRequest.purpose == "signup",
        OTPRequest.role == "HR",
        OTPRequest.verified_at.isnot(None),
        OTPRequest.verified_at >= datetime.utcnow() - timedelta(minutes=OTP_FRESH_MIN)
    ).first()

    if not otp:
        raise HTTPException(403, "EMAIL_VERIFICATION_REQUIRED")

    # 🔐 Prevent duplicates
    if db.query(UserDetail).filter(
        (UserDetail.employee_id == payload.employee_id) |
        (UserDetail.user_name == payload.user_name)
    ).first():
        raise HTTPException(409, "USER_ALREADY_EXISTS")

    # 🔐 BLOCK SUPERADMIN ROLE FROM THIS ROUTE
    if payload.profile.role_id:
        role = db.query(UserRole).filter(
            UserRole.role_id == payload.profile.role_id
        ).first()

        if role and role.role_name.upper() == "SUPERADMIN":
            raise HTTPException(
                status_code=403,
                detail="SUPERADMIN_CREATION_NOT_ALLOWED_HERE"
            )

    # 🔐 FORCE ROLE = HR (never trust frontend)
    hr_role = db.query(UserRole).filter(
        UserRole.role_name == "HR"
    ).first()

    if not hr_role:
        raise HTTPException(500, "HR_ROLE_NOT_FOUND")

    # Create UserDetail
    user = UserDetail(
        employee_id=payload.employee_id,
        user_name=payload.user_name,
        password=get_password_hash(payload.password),
        is_active=1,
    )

    # Remove role_id from payload to avoid spoofing
    profile_data = payload.profile.dict(exclude={"role_id"})

    profile = ProfileInformation(
        employee_id=payload.employee_id,
        role_id=hr_role.role_id,
        **profile_data,
    )

    db.add_all([user, profile])
    db.commit()
    db.refresh(user)
    db.refresh(profile)

    return {"user": user, "profile": profile}


# ==============================================================================
# UPDATE USER DETAILS (SUPERADMIN ONLY)
# ==============================================================================
@router.put("/{employee_id}", response_model=UserDetailOut)
def update_user_details(
    employee_id: str,
    payload: UserDetailUpdate,
    db: Session = Depends(get_db),
    _=Depends(superadmin_session_required),
):
    user = db.query(UserDetail).filter(UserDetail.employee_id == employee_id).first()
    if not user:
        raise HTTPException(404, "USER_NOT_FOUND")

    if payload.user_name and payload.user_name != user.user_name:
        if db.query(UserDetail).filter(UserDetail.user_name == payload.user_name).first():
            raise HTTPException(409, "USERNAME_EXISTS")
        user.user_name = payload.user_name

    if payload.password:
        user.password = get_password_hash(payload.password)

    db.commit()
    db.refresh(user)
    return user


# ==============================================================================
# UPDATE PROFILE (HR SELF / SUPERADMIN ANY)
# ==============================================================================
@router.put("/{employee_id}/profile", response_model=ProfileOut)
def update_profile(
    employee_id: str,
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user_id: str = Depends(hr_session_required),
):
    current_role = _get_role(db, current_user_id)

    profile = db.query(ProfileInformation).filter(
        ProfileInformation.employee_id == employee_id
    ).first()

    if not profile:
        raise HTTPException(404, "PROFILE_NOT_FOUND")

    if current_role == "HR" and current_user_id != employee_id:
        raise HTTPException(403, "HR_CANNOT_EDIT_OTHERS_PROFILE")

    update_data = payload.dict(exclude_unset=True)

    # 🔐 Email change requires OTP (HR only)
    if "email" in update_data and isinstance(update_data["email"], dict):
        new_primary = update_data["email"].get("primary")
        old_primary = profile.email.get("primary") if profile.email else None

        if new_primary and new_primary != old_primary:
            old_otp = db.query(OTPRequest).filter(
                OTPRequest.email == old_primary,
                OTPRequest.purpose == "email_change_old",
                OTPRequest.role == "HR",
                OTPRequest.verified_at.isnot(None),
                OTPRequest.verified_at >= datetime.utcnow() - timedelta(minutes=OTP_FRESH_MIN)
            ).first()

            new_otp = db.query(OTPRequest).filter(
                OTPRequest.email == new_primary,
                OTPRequest.purpose == "email_change_new",
                OTPRequest.role == "HR",
                OTPRequest.verified_at.isnot(None),
                OTPRequest.verified_at >= datetime.utcnow() - timedelta(minutes=OTP_FRESH_MIN)
            ).first()

            if not old_otp or not new_otp:
                raise HTTPException(403, "BOTH_OTP_REQUIRED_TO_UPDATE_EMAIL")

    for key, value in update_data.items():
        setattr(profile, key, value)

    db.commit()
    db.refresh(profile)
    return profile


# ==============================================================================
# GET USER / GET ALL USERS (PAGINATED)
# ==============================================================================
@router.get("/", response_model=PaginatedUserResponse)
def get_users(
    employee_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50, alias="limit"),
    db: Session = Depends(get_db),
    current_user_id: str = Depends(hr_session_required),
):
    current_role = _get_role(db, current_user_id)

    if current_role == "HR":
        employee_id = current_user_id

    query = (
        db.query(UserDetail)
        .options(joinedload(UserDetail.profile_information))
    )

    if employee_id:
        query = query.filter(UserDetail.employee_id == employee_id)

    total = query.count()

    users = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        UserWithProfileOut(user=u, profile=u.profile_information)
        for u in users
        if u.profile_information
    ]

    return PaginatedUserResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_next=(page * page_size) < total,
    )


# ==============================================================================
# DELETE USER (SUPERADMIN ONLY, SUPERADMIN PROTECTED)
# ==============================================================================
@router.delete("/{employee_id}", status_code=200)
def delete_user(
    employee_id: str,
    db: Session = Depends(get_db),
    _=Depends(superadmin_session_required),
):
    profile = (
        db.query(ProfileInformation)
        .options(joinedload(ProfileInformation.role))
        .filter(ProfileInformation.employee_id == employee_id)
        .first()
    )

    if not profile or not profile.role:
        raise HTTPException(404, "USER_NOT_FOUND")

    if profile.role.role_name.upper() == "SUPERADMIN":
        raise HTTPException(403, "SUPERADMIN_CANNOT_BE_DELETED")

    try:
        db.delete(profile)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, "ERROR_DELETING_USER")

    return {"message": f"{employee_id} deleted successfully"}
