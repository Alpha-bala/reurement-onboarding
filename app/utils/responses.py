# app/utils/core/responses.py
from __future__ import annotations

from typing import Any, Optional, Dict
from pydantic import BaseModel, Field


# ============================================================
# BASE RESPONSE
# ============================================================
class APIResponse(BaseModel):
    """
    Standard API response envelope.
    Used across all endpoints.
    """
    success: bool = Field(..., description="Indicates request success or failure")
    message: str = Field(..., description="Human-readable message")
    data: Optional[Any] = Field(None, description="Response payload")
    errors: Optional[Dict[str, Any]] = Field(None, description="Error details (if any)")


# ============================================================
# SUCCESS RESPONSE HELPERS
# ============================================================
def success_response(
    *,
    message: str = "Success",
    data: Any = None,
) -> APIResponse:
    """
    Returns a standard success response.
    """
    return APIResponse(
        success=True,
        message=message,
        data=data,
        errors=None,
    )


# ============================================================
# ERROR RESPONSE HELPERS
# ============================================================
def error_response(
    *,
    message: str = "Something went wrong",
    errors: Optional[Dict[str, Any]] = None,
) -> APIResponse:
    """
    Returns a standard error response.
    """
    return APIResponse(
        success=False,
        message=message,
        data=None,
        errors=errors,
    )
