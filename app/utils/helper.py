from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Optional, Set
import os

from dotenv import load_dotenv
from jose import jwt, JWTError, ExpiredSignatureError
from passlib.context import CryptContext
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer

# ================================================================
# CONFIGURATION
# ================================================================
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "fallback_super_secret_key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    default="argon2",
    deprecated=["bcrypt"],
)


# ================================================================
# 🧩 OAuth Schemes
# ================================================================
oauth2_candidate = OAuth2PasswordBearer(
    tokenUrl="/candidates/login", scheme_name="CandidateAuth", auto_error=False
)
oauth2_hr = OAuth2PasswordBearer(
    tokenUrl="/auth/hr/login", scheme_name="HRAuth", auto_error=False
)
oauth2_superadmin = OAuth2PasswordBearer(
    tokenUrl="/auth/superadmin/login", scheme_name="SuperadminAuth", auto_error=False
)


# ================================================================
# PASSWORD HELPERS
# ================================================================
def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False

    try:
        valid = pwd_context.verify(plain_password, hashed_password)

        # OPTIONAL: auto-upgrade bcrypt → argon2 on login
        if valid and pwd_context.needs_update(hashed_password):
            new_hash = pwd_context.hash(plain_password)
            # save new_hash to DB in calling service
            # user.password = new_hash
            # db.commit()

        return valid
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    if not password:
        raise HTTPException(status_code=400, detail="PASSWORD_REQUIRED")

    return pwd_context.hash(password)


# ================================================================
# 🔐 JWT HELPERS
# ================================================================
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode.update({
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access"
    })

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ================================================================
# 🔍 Unified token getter
# ================================================================
def _get_any_token(
        t1: Optional[str] = Depends(oauth2_candidate),
        t2: Optional[str] = Depends(oauth2_hr),
        t3: Optional[str] = Depends(oauth2_superadmin),
) -> str:
    token = t1 or t2 or t3
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return token


# ================================================================
# 🔐 ROLE GUARDS — Updated MFA Logic (NO GLOBAL MFA)
# ================================================================
def require_roles(*allowed_roles: str):
    allowed: Set[str] = {r.upper() for r in allowed_roles}

    def _dep(token: str = Depends(_get_any_token)) -> str:
        payload = decode_access_token(token)

        role = (payload.get("role") or "").upper()
        sub = (payload.get("sub") or "").strip()

        # ❗ TEMP MFA TICKETS ARE NOT ALLOWED TO ACCESS ROUTES
        if payload.get("token_use") == "mfa_ticket":
            raise HTTPException(status_code=403, detail="MFA verification required")

        # Role checking
        if allowed and role not in allowed:
            raise HTTPException(status_code=403, detail=f"Forbidden for role: {role}")

        # Valid user session required
        if not payload.get("session_id"):
            raise HTTPException(status_code=401, detail="Invalid session")

        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token")

        return sub

    return _dep


# ================================================================
# Role-specific guards
# ================================================================
def require_roles_from_scheme(oauth_scheme: OAuth2PasswordBearer, *allowed_roles: str):
    allowed: Set[str] = {r.upper() for r in allowed_roles}

    def _dep(token: Optional[str] = Depends(oauth_scheme)) -> str:
        if not token:
            raise HTTPException(status_code=401, detail="Missing token")

        payload = decode_access_token(token)

        role = (payload.get("role") or "").upper()
        sub = (payload.get("sub") or "")

        # Block MFA temp tickets
        if payload.get("token_use") == "mfa_ticket":
            raise HTTPException(status_code=403, detail="MFA verification required")

        if allowed and role not in allowed:
            raise HTTPException(status_code=403, detail=f"Forbidden for role: {role}")

        if not payload.get("session_id"):
            raise HTTPException(status_code=401, detail="Invalid session")

        return sub

    return _dep


# ================================================================
# Convenience Guards
# ================================================================
require_candidate_only = require_roles_from_scheme(oauth2_candidate, "CANDIDATE")
require_hr_only = require_roles_from_scheme(oauth2_hr, "HR")
require_superadmin_only = require_roles_from_scheme(oauth2_superadmin, "SUPERADMIN")


# HR OR SUPERADMIN
def _token_hr_or_sa(
        t_hr: Optional[str] = Depends(oauth2_hr),
        t_sa: Optional[str] = Depends(oauth2_superadmin),
) -> str:
    token = t_hr or t_sa
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return token


def require_hr_or_superadmin(token: str = Depends(_token_hr_or_sa)) -> str:
    payload = decode_access_token(token)

    if payload.get("token_use") == "mfa_ticket":
        raise HTTPException(status_code=403, detail="MFA verification required")

    role = (payload.get("role") or "").upper()
    sub = payload.get("sub") or ""

    if role not in {"HR", "SUPERADMIN"}:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not payload.get("session_id"):
        raise HTTPException(status_code=401, detail="Invalid session")

    return sub


# ================================================================
# EXPORT
# ================================================================
require = SimpleNamespace(
    candidate_only=require_candidate_only,
    hr_only=require_hr_only,
    superadmin_only=require_superadmin_only,
    hr_or_superadmin=require_hr_or_superadmin,
    any=require_roles(),
)

