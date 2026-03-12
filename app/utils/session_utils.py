import uuid
import datetime
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user_models import UserSession
from app.utils.helper import decode_access_token
from datetime import timezone, timedelta

# ===============================================================
# 🕒 Timezone Handling (IST = UTC +5:30)
# ===============================================================
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    """Return current time in IST (naive datetime for DB compatibility)."""
    return datetime.datetime.now(IST).replace(tzinfo=None)


# ===============================================================
# 1️⃣ Create or Replace Session (Called After Successful MFA/Login)
# ===============================================================
def create_user_session(
        db: Session,
        *,
        user_id: str,
        role_type: str,
        jwt_token: str | None = None,  # ✅ made optional for MFA flow
        expires_minutes: int = 1440  # default = 1 day
) -> dict:
    """
    Creates a new user session record.

    - Ensures only one active session per user (single-session policy).
    - Deactivates previous sessions.
    - Supports session creation before JWT is generated (token can be added later).
    """

    # Step 1 — Deactivate any existing active sessions
    db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_active == True
    ).update({
        "is_active": False,
        "logout_time": now_ist(),
        "jwt_token": None  # 🔥 Clear previous tokens
    })
    db.commit()

    # Step 2 — Create new session
    ist_now = now_ist()
    new_session = UserSession(
        session_id=str(uuid.uuid4()),
        user_id=user_id,
        role_type=role_type.upper(),
        jwt_token=jwt_token,
        login_time=ist_now,
        last_activity_time=ist_now,
        expires_at=ist_now + datetime.timedelta(minutes=expires_minutes),
        is_active=True
    )

    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    # ✅ Return clean response
    return {
        "session_id": new_session.session_id,
        "user_id": user_id,
        "role": role_type.upper(),
        "login_time": new_session.login_time.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": new_session.expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        "message": f"{role_type} {user_id} logged in successfully"
    }


# ===============================================================
# 2️⃣ Verify Active Session (Used in Protected Routes)
# ===============================================================
def verify_active_session(db: Session, *, token: str) -> UserSession:
    """
    Verifies that the given JWT token corresponds to an active, valid session.
    Also refreshes 'last_activity_time' on each request.
    """

    payload = decode_access_token(token)
    user_id = payload.get("sub")
    role_type = payload.get("role")
    session_id = payload.get("session_id")

    if not user_id or not role_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    # 🔍 Match active session by token and user
    session = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.jwt_token == token,
        UserSession.is_active == True
    ).first()

    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    # 🔐 Validate expiration
    if session.expires_at < now_ist():
        session.is_active = False
        session.logout_time = now_ist()
        session.jwt_token = None
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    # 🔄 Refresh activity timestamp
    session.last_activity_time = now_ist()
    db.commit()

    return session


# ===============================================================
# 3️⃣ Logout (Manual Logout or Forced End)
# ===============================================================
def logout_user_session(db: Session, *, token: str) -> dict:
    """
    Marks the user's session (identified by JWT) as logged out.
    Clears stored JWT and updates logout timestamp.
    """

    payload = decode_access_token(token)
    user_id = payload.get("sub")
    role_type = payload.get("role")

    session = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.jwt_token == token,
        UserSession.is_active == True
    ).first()

    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active session not found")

    # 🔒 End session
    session.is_active = False
    session.logout_time = now_ist()
    session.jwt_token = None
    db.commit()

    return {
        "message": f"{role_type} {user_id} logged out successfully",
        "login_time": session.login_time.strftime("%Y-%m-%d %H:%M:%S"),
        "logout_time": session.logout_time.strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session.session_id,
        "user_id": user_id,
        "role": role_type
    }

