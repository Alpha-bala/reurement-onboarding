# app/schemas/user_profile_schemas.py
from __future__ import annotations

from typing import Optional, List, Dict, Any
from typing_extensions import Annotated
from datetime import date

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    ConfigDict,
    StringConstraints,
)

# ============================================================
# COMMON STRING TYPES (REUSABLE)
# ============================================================
NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ShortStr20 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]
ShortStr50 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]

# ===============================================================
# super admin request
# ===============================================================
class SuperAdminCreateRequest(BaseModel):
    employee_id: str
    user_name: EmailStr
    password: str
    first_name: str
    last_name: str
    email: Optional[Dict[str, Any]] = None
    
# ============================================================
# PROFILE BASE
# ============================================================
class ProfileBase(BaseModel):
    first_name: NameStr
    last_name: NameStr

    email: Optional[Dict[str, Any]] = None

    # MUST be Optional because DB has NULL
    contact_number: Optional[List[Dict[str, str]]] = None
    emergency_contact_number: Optional[List[Dict[str, str]]] = None

    gender: Optional[ShortStr20] = None
    date_of_birth: Optional[date] = None
    department_id: Optional[ShortStr50] = None
    role_id: Optional[ShortStr50] = None
    hire_date: Optional[date] = None



# ============================================================
# PROFILE UPDATE (ALL OPTIONAL)
# ============================================================
class ProfileUpdate(BaseModel):
    first_name: Optional[NameStr] = None
    last_name: Optional[NameStr] = None

    email: Optional[Dict[str, Any]] = None
    contact_number: Optional[List[Dict[str, str]]] = None
    emergency_contact_number: Optional[List[Dict[str, str]]] = None

    gender: Optional[ShortStr20] = None
    date_of_birth: Optional[date] = None
    department_id: Optional[ShortStr50] = None
    role_id: Optional[ShortStr50] = None
    hire_date: Optional[date] = None


# ============================================================
# PROFILE OUT
# ============================================================
from pydantic import model_validator

class ProfileOut(ProfileBase):
    employee_id: str

    @model_validator(mode="after")
    def normalize_null_lists(self):
        self.contact_number = self.contact_number or []
        self.emergency_contact_number = self.emergency_contact_number or []
        return self

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# USER CREATE (SUPERADMIN)
# ============================================================
class UserCreate(BaseModel):
    employee_id: ShortStr50
    user_name: NameStr
    password: Annotated[str, StringConstraints(min_length=6, max_length=255)]
    is_active: int = Field(1, ge=0, le=1)

    profile: ProfileBase


# ============================================================
# USER DETAIL UPDATE (SUPERADMIN)
# ============================================================
class UserDetailUpdate(BaseModel):
    user_name: Optional[NameStr] = None
    password: Optional[Annotated[str, StringConstraints(min_length=6, max_length=255)]] = None
    is_active: Optional[int] = Field(None, ge=0, le=1)


# ============================================================
# USER DETAIL OUT
# ============================================================
class UserDetailOut(BaseModel):
    employee_id: str
    user_name: str
    is_active: int

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# USER + PROFILE OUT
# ============================================================
class UserWithProfileOut(BaseModel):
    user: UserDetailOut
    profile: ProfileOut

    model_config = ConfigDict(from_attributes=True)


from typing import List
from pydantic import BaseModel

class PaginatedUserResponse(BaseModel):
    items: List[UserWithProfileOut]
    page: int
    page_size: int
    total: int
    has_next: bool
