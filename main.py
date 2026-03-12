from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import os

from dotenv import load_dotenv, find_dotenv

# ===============================================================
# Load Environment Variables
# ===============================================================
load_dotenv(find_dotenv(), override=True)

# ===============================================================
# Database
# ===============================================================
from app import database
from app.database import Base

# ===============================================================
# Session / Guards
# ===============================================================
from app.utils.session_activity import update_last_activity
from app.utils.guards import (
    candidate_session_required,
    hr_session_required,
    superadmin_session_required,
)

# ===============================================================
# Routers
# ===============================================================
from app.routers import (
    candidates,
    jobs,
    profile,
    screenings,
    interviews,
    roles_departments,
    user_admin,
    superadmin_login,
    auth_hr,
    admin_documents,
    bgv_admin,
    bgv_candidate,
    candidate_offer,
    candidate_documents,
    doc_config,
    admin_offer,
    notifications,
    panel_feedback,
    admin,
    saved_jobs,
    mfa,
    hr_otp,
    company,
    public_apis,
)

# ===============================================================
# FastAPI App Initialization
# ===============================================================
app = FastAPI()

# ===============================================================
# Database Table Creation
# (Use Alembic in production — OK for dev/testing)
# ===============================================================
Base.metadata.create_all(bind=database.engine)

# ===============================================================
# Middleware — Track Last Activity
# ===============================================================
app.middleware("http")(update_last_activity)

# ===============================================================
# Register Routers
# ===============================================================
app.include_router(candidates.router)
app.include_router(jobs.router)
app.include_router(profile.router)
app.include_router(screenings.router)
app.include_router(interviews.router)
app.include_router(roles_departments.router)
app.include_router(user_admin.router)
app.include_router(superadmin_login.router)
app.include_router(auth_hr.router)
app.include_router(admin_documents.router)
app.include_router(candidate_documents.router)
app.include_router(candidate_offer.router)
app.include_router(doc_config.router)
app.include_router(bgv_candidate.router)
app.include_router(bgv_admin.router)
app.include_router(admin_offer.router)
app.include_router(notifications.router)
app.include_router(panel_feedback.panel_feedback_router)
app.include_router(admin.admin_interviews_router)
app.include_router(admin.admin_applications_router)
app.include_router(saved_jobs.router)
app.include_router(mfa.router)
app.include_router(hr_otp.router)
app.include_router(company.router)
app.include_router(public_apis.router)

# ===============================================================
# CORS Configuration
# ===============================================================
ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================
# Test / Health Authorization Endpoints
# ===============================================================
@app.get("/me")
def get_my_profile(candidate_id: str = Depends(candidate_session_required)):
    return {
        "candidate_id": candidate_id,
        "message": "Candidate authorized ✅",
    }


@app.get("/hr/dashboard")
def hr_dashboard(employee_id: str = Depends(hr_session_required)):
    return {
        "employee_id": employee_id,
        "message": "HR authorized ✅",
    }


@app.get("/superadmin/stats")
def superadmin_dashboard(superadmin_id: str = Depends(superadmin_session_required)):
    return {
        "superadmin_id": superadmin_id,
        "message": "Superadmin authorized ✅",
    }
