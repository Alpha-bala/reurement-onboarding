# # app/middleware/session_activity.py
# from fastapi import Request
# from datetime import timedelta
# from app.database import SessionLocal
# from app.models.user_models import UserSession
# from app.utils.session_utils import now_ist

# # Idle timeout in minutes
# IDLE_TIMEOUT_MINUTES = 30


# def extract_token_from_header(headers) -> str | None:
#     """
#     Extract Bearer token from Authorization header.
#     Example: Authorization: Bearer <token>
#     """
#     auth = headers.get("authorization")
#     if not auth or not auth.lower().startswith("bearer "):
#         return None
#     return auth.split(" ")[1].strip()


# async def update_last_activity(request: Request, call_next):
#     """
#     Middleware that tracks active user sessions.
#     BUT — WebSocket handshake requests MUST be skipped.
#     """

#     # ✅ FIX: Skip middleware for WebSocket handshake (prevents 403 error)
#     if request.headers.get("upgrade", "").lower() == "websocket":
#         return await call_next(request)

#     token = extract_token_from_header(request.headers)

#     if token:
#         db = SessionLocal()
#         try:
#             session = db.query(UserSession).filter(
#                 UserSession.jwt_token == token,
#                 UserSession.is_active == True
#             ).first()

#             if session:
#                 now = now_ist()

#                 # 🔥 Hard expiration
#                 if session.expires_at < now:
#                     session.is_active = False
#                     session.logout_time = now
#                     session.jwt_token = None
#                     db.commit()
#                     db.close()

#                     from fastapi.responses import JSONResponse
#                     return JSONResponse(
#                         status_code=401,
#                         content={"detail": "Session expired"}
#                     )

#                 # ⏳ Idle timeout check
#                 if (now - session.last_activity_time) > timedelta(minutes=IDLE_TIMEOUT_MINUTES):
#                     session.is_active = False
#                     session.logout_time = now
#                     session.jwt_token = None
#                     db.commit()
#                     db.close()

#                     from fastapi.responses import JSONResponse
#                     return JSONResponse(
#                         status_code=401,
#                         content={"detail": "Session expired due to inactivity"}
#                     )

#                 # 🔁 Update last activity timestamp
#                 session.last_activity_time = now
#                 db.commit()

#         except Exception as e:
#             print(f"[Session middleware] Error: {e}")

#         finally:
#             db.close()

#     # Continue request
#     response = await call_next(request)
#     return response


# app/middleware/session_activity.py
from fastapi import Request
from datetime import timedelta
from app.database import SessionLocal
from app.models.user_models import UserSession
from app.utils.session_utils import now_ist
from fastapi.responses import JSONResponse

IDLE_TIMEOUT_MINUTES = 30


def extract_token_from_header(headers) -> str | None:
    auth = headers.get("authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ")[1].strip()


async def update_last_activity(request: Request, call_next):

    # ✅ Skip WebSocket handshake
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)

    token = extract_token_from_header(request.headers)

    if token:
        db = SessionLocal()
        try:
            session = db.query(UserSession).filter(
                UserSession.jwt_token == token,
                UserSession.is_active == True
            ).first()

            if session:
                now = now_ist()

                # 🔥 Hard expiry check
                if session.expires_at < now:
                    session.is_active = False
                    session.logout_time = now
                    session.jwt_token = None
                    db.commit()
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Session expired"}
                    )

                # ⏳ Idle timeout check ONLY (no update here)
                if session.last_activity_time and \
                   (now - session.last_activity_time) > timedelta(minutes=IDLE_TIMEOUT_MINUTES):

                    session.is_active = False
                    session.logout_time = now
                    session.jwt_token = None
                    db.commit()
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Session expired due to inactivity"}
                    )

        except Exception as e:
            print(f"[Session middleware] Error: {e}")

        finally:
            db.close()

    return await call_next(request)
