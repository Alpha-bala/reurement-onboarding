# app/utils/core/exceptions.py
from __future__ import annotations

from typing import Optional, Dict, Any


# ============================================================
# BASE APPLICATION EXCEPTION
# ============================================================
class AppException(Exception):
    """
    Base exception for all application-level errors.
    """
    status_code: int = 500
    message: str = "Application error"
    errors: Optional[Dict[str, Any]] = None

    def __init__(
        self,
        *,
        message: Optional[str] = None,
        errors: Optional[Dict[str, Any]] = None,
    ):
        if message:
            self.message = message
        self.errors = errors
        super().__init__(self.message)


# ============================================================
# COMMON HTTP EXCEPTIONS
# ============================================================
class BadRequest(AppException):
    status_code = 400
    message = "Bad request"


class Unauthorized(AppException):
    status_code = 401
    message = "Unauthorized"


class Forbidden(AppException):
    status_code = 403
    message = "Forbidden"


class NotFound(AppException):
    status_code = 404
    message = "Resource not found"


class Conflict(AppException):
    status_code = 409
    message = "Conflict"


class UnprocessableEntity(AppException):
    status_code = 422
    message = "Unprocessable entity"


class InternalServerError(AppException):
    status_code = 500
    message = "Internal server error"
