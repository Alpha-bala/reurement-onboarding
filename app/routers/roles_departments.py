from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import logging

from app.database import get_db
from app.models.auth_models import UserRole, Department
from app.schemas.roles_departments_schemas import (
    RoleCreate, RoleUpdate, RoleOut, RoleListOut,
    DepartmentCreate, DepartmentUpdate, DepartmentOut, DepartmentListOut
)

logger = logging.getLogger("app.routers.roles_departments")
logger.setLevel(logging.INFO)

router = APIRouter(
    prefix="/admin/roles-departments",
    tags=["Admin: Roles & Departments"]
)

# ===============================================================
# ROLES CRUD (Create, Read, Update, Delete)
# ===============================================================

@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate, db: Session = Depends(get_db)):
    if db.query(UserRole).filter(UserRole.role_id == payload.role_id).first():
        raise HTTPException(status_code=409, detail="role_id already exists")
    if db.query(UserRole).filter(UserRole.role_name == payload.role_name).first():
        raise HTTPException(status_code=409, detail="role_name already exists")

    role = UserRole(
        role_id=payload.role_id,
        role_name=payload.role_name,
        description=payload.description,
    )
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Unique constraint error while creating role")
    db.refresh(role)

    logger.info(f"[Roles] Role created → {payload.role_id}")
    return role


@router.get("/roles/{role_id}", response_model=RoleOut)
def get_role(role_id: str, db: Session = Depends(get_db)):
    role = db.query(UserRole).filter(UserRole.role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    logger.info(f"[Roles] Role fetched → {role_id}")
    return role


@router.get("/roles", response_model=RoleListOut)
def list_roles(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None, description="Search by role_name contains"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    query = db.query(UserRole)
    if q:
        query = query.filter(UserRole.role_name.ilike(f"%{q}%"))
    total = query.count()
    items = query.order_by(UserRole.role_name.asc()).offset(skip).limit(limit).all()

    logger.info(f"[Roles] Listing roles (total={total})")
    return {"items": items, "total": total}


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: str, payload: RoleUpdate, db: Session = Depends(get_db)):
    role = db.query(UserRole).filter(UserRole.role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Uniqueness for new role_name
    if payload.role_name and payload.role_name != role.role_name:
        if db.query(UserRole).filter(UserRole.role_name == payload.role_name).first():
            raise HTTPException(status_code=409, detail="role_name already exists")

    if payload.role_name is not None:
        role.role_name = payload.role_name
    if payload.description is not None:
        role.description = payload.description

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Unique constraint error while updating role")
    db.refresh(role)

    logger.info(f"[Roles] Role updated → {role_id}")
    return role


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: str, db: Session = Depends(get_db)):
    """Delete a role (if not referenced)."""
    role = db.query(UserRole).filter(UserRole.role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    db.delete(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cannot delete role: it is referenced by other records",
        )

    logger.info(f"[Roles] Role deleted → {role_id}")
    return None


# ===============================================================
# DEPARTMENTS CRUD (Create, Read, Update, Delete)
# ===============================================================

@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentCreate, db: Session = Depends(get_db)):
    """Create a new department (SUPERADMIN only)."""
    if db.query(Department).filter(Department.department_id == payload.department_id).first():
        raise HTTPException(status_code=409, detail="department_id already exists")
    if db.query(Department).filter(Department.department_name == payload.department_name).first():
        raise HTTPException(status_code=409, detail="department_name already exists")

    dept = Department(
        department_id=payload.department_id,
        department_name=payload.department_name,
        description=payload.description,
    )
    db.add(dept)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Unique constraint error while creating department")
    db.refresh(dept)

    logger.info(f"[Departments] Department created → {payload.department_id}")
    return dept


@router.get("/departments/{department_id}", response_model=DepartmentOut)
def get_department(department_id: str, db: Session = Depends(get_db)):
    dept = db.query(Department).filter(Department.department_id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    logger.info(f"[Departments] Department fetched → {department_id}")
    return dept


@router.get("/departments", response_model=DepartmentListOut)
def list_departments(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(None, description="Search by department_name contains"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    query = db.query(Department)
    if q:
        query = query.filter(Department.department_name.ilike(f"%{q}%"))
    total = query.count()
    items = query.order_by(Department.department_name.asc()).offset(skip).limit(limit).all()

    logger.info(f"[Departments] Listing departments (total={total})")
    return {"items": items, "total": total}


@router.put("/departments/{department_id}", response_model=DepartmentOut)
def update_department(department_id: str, payload: DepartmentUpdate, db: Session = Depends(get_db)):
    dept = db.query(Department).filter(Department.department_id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    if payload.department_name and payload.department_name != dept.department_name:
        if db.query(Department).filter(Department.department_name == payload.department_name).first():
            raise HTTPException(status_code=409, detail="department_name already exists")

    if payload.department_name is not None:
        dept.department_name = payload.department_name
    if payload.description is not None:
        dept.description = payload.description

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Unique constraint error while updating department")
    db.refresh(dept)

    logger.info(f"[Departments] Department updated → {department_id}")
    return dept


@router.delete("/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(department_id: str, db: Session = Depends(get_db)):
    dept = db.query(Department).filter(Department.department_id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    db.delete(dept)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cannot delete department: it is referenced by other records",
        )

    logger.info(f"[Departments] Department deleted → {department_id}")
    return None
