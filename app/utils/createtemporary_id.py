# app/utils/createtemporary_id.py
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.database import SessionLocal  # your existing Session factory
from app.models.offer_tables import TemperoryIdStorage  # ensure correct import path


# -------------------------------
# Helpers
# -------------------------------
def generate_employee_id(last_profile_id: Optional[str] = None, company_prefix: str = "SX") -> str:
    """
    Build a new sequential employee code: {prefix}{year}{nn}
    Examples: SX202501, SX202502, ...
    """
    current_year = date.today().year

    if last_profile_id:
        # strip prefix once, then slice off the 4-digit year; remainder is the numeric counter
        try:
            num = int(last_profile_id.replace(company_prefix, "", 1)[4:]) + 1
        except Exception:
            # fallback: if something odd is stored, just start from 1
            num = 1
    else:
        num = 1

    # pad to 2 digits (01, 02, ...). Change to 3/4 if you expect more volume.
    num_str = str(num).zfill(2)
    return f"{company_prefix}{current_year}{num_str}"


# -------------------------------
# Main API
# -------------------------------
def generate_temporary_employee_id(limit_days: int = 30, db: Session | None = None) -> str:
    """
    Returns a temporary employee id from TemperoryIdStorage.
      - Reopens any 'hold' IDs older than `limit_days`
      - Reuses the smallest 'open' ID if available
      - Otherwise creates a new sequential ID (SX{year}{nn})
    Uses the provided SQLAlchemy Session if given; otherwise opens/closes its own.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        # 1) Move old 'hold' IDs back to 'open'
        db.query(TemperoryIdStorage).filter(
            func.datediff(datetime.now(), TemperoryIdStorage.created_at) > limit_days,
            TemperoryIdStorage.status == "hold",
        ).update({"status": "open"}, synchronize_session=False)
        db.commit()

        # 2) Reuse the smallest 'open' temporary id (if any)
        open_row = (
            db.query(TemperoryIdStorage.temporary_id)
            .filter(TemperoryIdStorage.status == "open")
            .order_by(TemperoryIdStorage.temporary_id.asc())
            .first()
        )
        if open_row:
            temp_id = open_row[0]
            db.query(TemperoryIdStorage).filter(
                TemperoryIdStorage.temporary_id == temp_id
            ).update({"status": "hold", "created_at": datetime.now()}, synchronize_session=False)
            db.commit()
            return temp_id

        # 3) Otherwise, generate a new one
        last_row = (
            db.query(TemperoryIdStorage.temporary_id)
            .order_by(TemperoryIdStorage.temporary_id.desc())
            .first()
        )
        last_temp_id = last_row[0] if last_row else None
        temp_id = generate_employee_id(last_temp_id)  # uses SX + year + NN

        db.add(TemperoryIdStorage(temporary_id=temp_id, status="hold", created_at=datetime.now()))
        db.commit()
        return temp_id

    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()






