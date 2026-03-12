# app/schemas/roles_departments_schemas.py
from __future__ import annotations

from typing import Optional, List
from typing_extensions import Annotated

from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
    StringConstraints,
)

# ============================================================
# COMMON STRING TYPES (REUSABLE)
# ============================================================
NameStr100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
DescStr255 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
IdStr50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


# ============================================================
# ROLE SCHEMAS
# ============================================================
class RoleBase(BaseModel):
    role_name: NameStr100 = Field(..., example="HR")
    description: Optional[DescStr255] = Field(
        None, example="Human Resources role"
    )


class RoleCreate(RoleBase):
    role_id: IdStr50 = Field(..., example="R2")


class RoleUpdate(BaseModel):
    role_name: Optional[NameStr100] = None
    description: Optional[DescStr255] = None


class RoleOut(RoleBase):
    role_id: str
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# DEPARTMENT SCHEMAS
# ============================================================
class DepartmentBase(BaseModel):
    department_name: NameStr100 = Field(..., example="Recruiting")
    description: Optional[DescStr255] = Field(
        None, example="Default recruiting group"
    )


class DepartmentCreate(DepartmentBase):
    department_id: IdStr50 = Field(..., example="DEPT01")


class DepartmentUpdate(BaseModel):
    department_name: Optional[NameStr100] = None
    description: Optional[DescStr255] = None


class DepartmentOut(DepartmentBase):
    department_id: str
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# LIST / PAGINATION WRAPPERS (FRONTEND READY)
# ============================================================
class RoleListOut(BaseModel):
    items: List[RoleOut]
    total: int


class DepartmentListOut(BaseModel):
    items: List[DepartmentOut]
    total: int
