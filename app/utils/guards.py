import logging
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.utils.helper import require
from app.utils.session_utils import verify_active_session
from app.database import get_db

# ===============================================================
# ⚙️ Logger Setup
# ===============================================================
logger = logging.getLogger("app.guards")
logger.setLevel(logging.INFO)

# Optional: configure handler if not already done globally
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# ===============================================================
# 🔐 Candidate Guard (Role + Active Session)
# ===============================================================
def candidate_session_required(
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: str = Depends(require.candidate_only),
):
    """
    Ensures user:
      ✅ has valid JWT with role = CANDIDATE
      ✅ has an active, non-expired session (single-session enforcement)
    Returns:
      candidate_id (str)
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    token = auth_header.split(" ")[1].strip()

    try:
        verify_active_session(db=db, token=token)
    except HTTPException as e:
        logger.warning(
            f"[CANDIDATE GUARD] Session invalid for {current_user_id} → {str(e.detail)}"
        )
        raise HTTPException(status_code=401, detail="Session expired or invalid") from e
    except Exception as e:
        logger.exception(f"[CANDIDATE GUARD] Internal session verification error: {e}")
        raise HTTPException(status_code=500, detail="Internal session verification error")

    # 🔍 Debug trace (only shows if DEBUG enabled)
    logger.debug(f"[CANDIDATE GUARD] Token OK | User={current_user_id}")
    logger.info(f"[CANDIDATE GUARD] Access granted → User={current_user_id}")

    return current_user_id


# ===============================================================
# 🧑‍💼 HR Guard (Role + Active Session)
# ===============================================================
def hr_session_required(
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: str = Depends(require.hr_or_superadmin),
):
    """
    Ensures user:
      ✅ has valid JWT with role = HR or SUPERADMIN
      ✅ has an active, non-expired session
    Returns:
      employee_id (str)
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    token = auth_header.split(" ")[1].strip()

    try:
        verify_active_session(db=db, token=token)
    except HTTPException as e:
        logger.warning(
            f"[HR GUARD] Session invalid for {current_user_id} → {str(e.detail)}"
        )
        raise HTTPException(status_code=401, detail="Session expired or invalid") from e
    except Exception as e:
        logger.exception(f"[HR GUARD] Internal session verification error: {e}")
        raise HTTPException(status_code=500, detail="Internal session verification error")

    logger.debug(f"[HR GUARD] Token OK | User={current_user_id}")
    logger.info(f"[HR GUARD] Access granted → User={current_user_id}")

    return current_user_id


# ===============================================================
# 🧩 Superadmin Guard (Role + Active Session)
# ===============================================================
def superadmin_session_required(
        request: Request,
        db: Session = Depends(get_db),
        current_user_id: str = Depends(require.superadmin_only),
):
    """
    Ensures user:
      ✅ has valid JWT with role = SUPERADMIN
      ✅ has an active, non-expired session
    Returns:
      superadmin_id (str)
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    token = auth_header.split(" ")[1].strip()

    try:
        verify_active_session(db=db, token=token)
    except HTTPException as e:
        logger.warning(
            f"[SUPERADMIN GUARD] Session invalid for {current_user_id} → {str(e.detail)}"
        )
        raise HTTPException(status_code=401, detail="Session expired or invalid") from e
    except Exception as e:
        logger.exception(f"[SUPERADMIN GUARD] Internal session verification error: {e}")
        raise HTTPException(status_code=500, detail="Internal session verification error")

    logger.debug(f"[SUPERADMIN GUARD] Token OK | User={current_user_id}")
    logger.info(f"[SUPERADMIN GUARD] Access granted → User={current_user_id}")

    return current_user_id

