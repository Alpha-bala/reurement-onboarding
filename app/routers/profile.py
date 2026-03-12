import os
import uuid
import logging
import mimetypes
from typing import List, Optional, Tuple, Literal, Iterable, Any, Dict
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc,func
from pydantic import BaseModel

from app.database import get_db, SessionLocal
from app.utils.helper import require_candidate_only
from app.utils.notify_depends import send_notification_to_employee as send_notification
from app.utils.s3_utils import _upload_to_s3, generate_http_url  # ← use both
from app.utils.guards import candidate_session_required
from app.schemas.user_schemas import (
    CandidateProfileUpdate,
    MyApplicationOut,
    MyApplicationJobOut,
    MessageOut,
    CandidateFullProfileOut,
    ApplyJobOut,
    NotificationStructure,
    NotificationMeta
)
from app.schemas.jobs_schemas import JobOut

from app.models.user_models import CandidateProfileInformation, CandidateDocument
from app.models.job_models import Application, Job, ApplicationSource

from app.services.screening_services import screen_resume

# Import SendGrid email sender
from app.utils.aws_email import aws_send_mail

logger = logging.getLogger(__name__)

# ===============================================================
# Router Setup — Role + Active Session Protected
# ===============================================================
router = APIRouter(
    prefix="/candidate/profile",
    tags=["Candidate Profile"]
)


# =============================================================================
# Email confirmation (AWS SES)
# =============================================================================
async def send_application_confirmation_email(
        receiver_email: str,
        first_name: Optional[str],
        job_id: str,
        application_id: str,
        is_referral: bool = False,
        referral_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Send confirmation email via AWS SES SMTP"""

    FROM_EMAIL = os.getenv("SES_SENDER", "careers@securxperts.com")

    name = first_name or "Candidate"

    if is_referral:
        subject = f"Referral Application Confirmation — Job {job_id} (App ID: {application_id})"
        ref_line = f"Referral ID: {referral_id}\n" if referral_id else ""
        intro = "Thank you for applying via a referral. Your application has been tagged accordingly."
    else:
        subject = f"Application Confirmation — Job {job_id} (Application ID: {application_id})"
        ref_line = ""
        intro = "Thank you for applying directly on our job portal."

    body = (
        f"Dear {name},\n\n"
        f"{intro}\n\n"
        f"Job ID: {job_id}\n"
        f"Application ID: {application_id}\n"
        f"{ref_line}"
        f"Applied At (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "We have received your application and started the review process.\n\n"
        "Regards,\nRecruitment Team"
    )

    try:
        await aws_send_mail(
            from_email=FROM_EMAIL,
            reciver_to=[receiver_email],
            subject=subject,
            body=body,
            body_type="plain",
        )
        return True, None

    except Exception as e:
        logger.error(f"[SES Confirmation Email Error] {e}")
        return False, str(e)



# =============================================================================
# Notification helper
# =============================================================================
def _notify_hr_application_sync(employee_id: str, notification: NotificationStructure) -> None:
    """
    Background-safe HR notification sender
    """
    import anyio
    from app.database import SessionLocal

    db = SessionLocal()

    try:
        logger.info(f"[notify-hr] Sending notification to employee={employee_id}")

        anyio.run(
            send_notification,
            employee_id,      # employee_id
            notification,     # NotificationStructure object
            db                # db session (MANDATORY)
        )

        logger.info(f"[notify-hr] Notification stored successfully")

    except Exception as e:
        logger.error(
            f"[notify-hr] FAILED for employee={employee_id}: {e}",
            exc_info=True
        )
    finally:
        db.close()


# =============================================================================
# Helpers
# =============================================================================
def _normalize_csv_or_iter(v: Optional[Any]) -> List[str]:
    items: List[str] = []
    if v is None:
        return items
    if isinstance(v, str):
        parts = [p.strip() for p in v.split(",")]
        items = [p.lower() for p in parts if p.strip()]
    elif isinstance(v, Iterable) and not isinstance(v, (bytes, bytearray, dict)):
        tmp = []
        for x in v:
            if x is None:
                continue
            if isinstance(x, str):
                tmp.append(x.strip())
            else:
                tmp.append(str(x).strip())
        items = [p.lower() for p in tmp if p]
    else:
        s = str(v).strip()
        if s:
            items = [s.lower()]
    return sorted(set(items))


def _job_skills(job: Job) -> List[str]:
    primary = _normalize_csv_or_iter(getattr(job, "primary_skills", None))
    secondary = _normalize_csv_or_iter(getattr(job, "secondary_skills", None))
    return sorted(set(primary) | set(secondary))


def _skills_csv(job: Job) -> str:
    p = [s.strip() for s in (getattr(job, "primary_skills", "") or "").split(",") if s.strip()]
    s = [s.strip() for s in (getattr(job, "secondary_skills", "") or "").split(",") if s.strip()]
    seen = set()
    ordered = []
    for item in p + s:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    return ", ".join(ordered)


def _candidate_registration_skills(cand: CandidateProfileInformation) -> List[str]:
    skills_json = getattr(cand, "skills", None) or {}
    if not isinstance(skills_json, dict):
        return _normalize_csv_or_iter(skills_json)
    primary = _normalize_csv_or_iter(
        skills_json.get("primary_skills", skills_json.get("primary"))
    )
    secondary = _normalize_csv_or_iter(
        skills_json.get("secondary_skills", skills_json.get("secondary"))
    )
    return sorted(set(primary) | set(secondary))


def _overlap(job_sk: List[str], cand_sk: List[str]) -> int:
    return len(set(job_sk) & set(cand_sk))


def _skills_to_schema(sk: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(sk, dict):
        return None
    return {
        "primary_skills": sk.get("primary_skills") or sk.get("primary"),
        "secondary_skills": sk.get("secondary_skills") or sk.get("secondary"),
    }


# =============================================================================
# Candidate: Jobs
# =============================================================================
@router.get("/jobs", response_model=List[JobOut])
def list_open_jobs(
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    return db.query(Job).filter(Job.job_status == "Open").all()


@router.get("/jobs/matched", response_model=List[JobOut])
def get_my_matched_jobs(
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    # Fetch candidate profile
    cand = (
        db.query(CandidateProfileInformation)
        .filter(CandidateProfileInformation.candidate_id == candidate_id)
        .first()
    )
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Normalize candidate skills
    cand_skills = {s.strip().lower() for s in _candidate_registration_skills(cand)}
    if not cand_skills:
        return []

    # Fetch open jobs (case-insensitive)
    jobs = db.query(Job).filter(func.lower(Job.status) == "open").all()

    # Normalize and compare skills
    def normalize(skills):
        if not skills:
            return set()
        return {s.strip().lower() for s in skills}

    def overlap(job_skills, candidate_skills):
        return len(normalize(job_skills) & normalize(candidate_skills))

    matched_jobs = [
        j for j in jobs if overlap(_job_skills(j), cand_skills) >= 1
    ]

    return matched_jobs


# =============================================================================
# Candidate: Profile update
# =============================================================================
@router.put("/update_profile", response_model=MessageOut)
def profile_update(
        request: CandidateProfileUpdate,
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    updated_request = request.model_dump(exclude_none=True)
    db_profile = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id
    ).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Candidate not found")

    for key, value in updated_request.items():
        setattr(db_profile, key, value)
    db.commit()
    return MessageOut(message="profile updated successfully")

# =============================================================================
# Candidate: Documents upload (S3 integrated; update replaces old file)
# =============================================================================
@router.put("/upload_documents", response_model=MessageOut)
def documents_upload(
        gov_id_type: Literal["Aadhar", "PAN", "passport"] = None,
        photo: UploadFile = None,
        resume: UploadFile = None,
        govt_id: UploadFile = None,
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    # --- Validate candidate ---
    db_profile = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id
    ).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if govt_id and not gov_id_type:
        raise HTTPException(status_code=400, detail="Government ID type is required")

    # 🔹 Fetch existing documents to enforce "3 mandatory once" rule
    existing_docs = db.query(CandidateDocument).filter(
        CandidateDocument.candidate_id == candidate_id
    ).all()

    has_photo = any((d.doc_name or "").lower() == "photo" for d in existing_docs)
    has_resume = any((d.doc_name or "").lower() == "resume" for d in existing_docs)
    has_govt_id = any((d.doc_name or "").lower() == "govt_id" for d in existing_docs)

    # State AFTER this request (existing OR newly uploaded)
    after_photo = has_photo or bool(photo)
    after_resume = has_resume or bool(resume)
    after_govt_id = has_govt_id or bool(govt_id)

    # Enforce: these 3 are compulsory at least once
    if not (has_photo and has_resume and has_govt_id):
        if not (after_photo and after_resume and after_govt_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    "photo, resume and govt_id are mandatory. "
                    "Please ensure all three are uploaded at least once."
                ),
            )

    # ------------------------------------------------------------------
    # Helper: upload to S3 and return ONLY s3://bucket/key (NO HTTP URL)
    # ------------------------------------------------------------------
    def _upload_and_store_s3_uri(upload: UploadFile, subdir: str) -> str:
        file_bytes = upload.file.read()
        content_type = (
            upload.content_type
            or mimetypes.guess_type(upload.filename)[0]
            or "application/octet-stream"
        )
        s3_key = f"candidates/{candidate_id}/{subdir}/{uuid.uuid4()}-{upload.filename}"
        s3_uri = _upload_to_s3(file_bytes, s3_key, content_type)
        return s3_uri

    # ------------------------------------------------------------------
    # Helper: replace or insert document record
    # ------------------------------------------------------------------
    def _replace_or_create(doc_name: str, upload: UploadFile, subdir: str, sub_name: str = None):
        s3_uri = _upload_and_store_s3_uri(upload, subdir)

        existing = db.query(CandidateDocument).filter(
            CandidateDocument.candidate_id == candidate_id,
            func.lower(CandidateDocument.doc_name) == doc_name.lower()
        ).first()

        if existing:
            # Replace old file reference
            existing.file_name = upload.filename
            existing.file_path = s3_uri
            existing.doc_sub_name = sub_name
            existing.uploaded_at = datetime.now()
        else:
            # Create new record
            db.add(CandidateDocument(
                candidate_id=candidate_id,
                doc_name=doc_name.lower(),
                doc_sub_name=sub_name,
                file_name=upload.filename,
                file_path=s3_uri,
                uploaded_at=datetime.now()
            ))
        db.flush()

    # --- Upload photo ---
    if photo:
        _replace_or_create("photo", photo, "photo")

    # --- Upload resume ---
    if resume:
        _replace_or_create("resume", resume, "resume")

    # --- Upload govt_id ---
    if govt_id:
        _replace_or_create("govt_id", govt_id, "govt_id", gov_id_type)

    db.commit()
    return MessageOut(message="Documents uploaded successfully (replaced old files if existed)")


# =============================================================================
# Candidate: Download uploaded document (Frontend-friendly & secure)
# =============================================================================
@router.get("/documents/{doc_id}/download")
def download_candidate_document(
    doc_id: int,
    db: Session = Depends(get_db),
    candidate_id: str = Depends(candidate_session_required),
):
    """
    Secure document download endpoint.
    - Works for BOTH s3:// and old https:// records
    - Returns a fresh HTTPS URL (1-year validity)
    - Frontend should simply open/redirect to the URL
    """

    # --------------------------------------------------
    # Validate document belongs to candidate
    # --------------------------------------------------
    doc = db.query(CandidateDocument).filter(
        CandidateDocument.doc_id == doc_id,
        CandidateDocument.candidate_id == candidate_id
    ).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.file_path:
        raise HTTPException(status_code=400, detail="Invalid document path")

    # --------------------------------------------------
    # Generate frontend-downloadable URL
    # --------------------------------------------------
    try:
        download_url = generate_http_url(
            doc.file_path,
            expires_in=31536000  # 1 year validity
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate download URL: {str(e)}"
        )

    # --------------------------------------------------
    #  Return frontend-friendly response
    # --------------------------------------------------
    return {
        "doc_id": doc.doc_id,
        "doc_name": doc.doc_name,
        "file_name": doc.file_name,
        "download_url": download_url
    }



# =============================================================================
# Candidate: Full profile
# =============================================================================
@router.get("/candidate/me", response_model=CandidateFullProfileOut)
def get_candidate_full_profile(
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    db_profile = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id
    ).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Candidate not found")

    db_documents = db.query(CandidateDocument).filter(
        CandidateDocument.candidate_id == candidate_id
    ).all()

    skills_out = _skills_to_schema(db_profile.skills)

    return {
        "candidate_id": db_profile.candidate_id,
        "first_name": db_profile.first_name,
        "last_name": db_profile.last_name,
        "email": db_profile.email,
        "contact_number": db_profile.contact_number,
        "date_of_birth": db_profile.date_of_birth,
        "gender": db_profile.gender,
        "address": db_profile.address,
        "highest_qualification": db_profile.highest_qualification,
        "education": db_profile.education,
        "skills": skills_out,
        "experience": db_profile.experience,
        "total_experience": db_profile.total_experience,
        "relevant_experience": db_profile.relevant_experience,
        "documents": [
            {
                "doc_id": doc.doc_id,
                "doc_name": doc.doc_name,
                "doc_sub_name": doc.doc_sub_name,
                "file_name": doc.file_name,
                "file_path": doc.file_path,  # now an HTTPS link
                "uploaded_at": doc.uploaded_at,
            }
            for doc in db_documents
        ]
    }


# =============================================================================
# Candidate: Apply flow
# =============================================================================
def _screen_application_bg(application_id: str) -> None:
    db = SessionLocal()
    try:
        screen_resume(db, application_id)
    except Exception as e:
        logger.error(f"[screening-bg] application {application_id}: {e}")
        db.rollback()
    finally:
        db.close()


def generate_application_id(db: Session) -> str:
    last_app = db.query(Application).order_by(desc(Application.id)).first()
    if not last_app or not last_app.application_id:
        return "APP001"
    try:
        last_num = int(last_app.application_id.replace("APP", "") or 0)
    except ValueError:
        last_num = last_app.id or 0
    new_num = last_num + 1
    return f"APP{str(new_num).zfill(3)}"


class ApplyJobIn(BaseModel):
    source: Literal["socialmedia", "linkedin", "naukri", "internal_employee"]
    referral_token: Optional[str] = None


def _has_doc(db: Session, candidate_id: str, doc_name: str) -> bool:
    return db.query(CandidateDocument).filter(
        CandidateDocument.candidate_id == candidate_id,
        CandidateDocument.doc_name == doc_name
    ).first() is not None


@router.post("/apply/{job_id}", response_model=ApplyJobOut)
async def apply_job(
        job_id: str,
        payload: ApplyJobIn,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    candidate = db.query(CandidateProfileInformation).filter(
        CandidateProfileInformation.candidate_id == candidate_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not all([candidate.first_name, candidate.last_name, candidate.email, candidate.contact_number]):
        raise HTTPException(status_code=400,
                            detail="Missing essential details. Please complete your basic profile first.")

    if not candidate.education or not isinstance(candidate.education, dict):
        raise HTTPException(status_code=400, detail="Profile incomplete: add your education details.")
    if not candidate.skills or not isinstance(candidate.skills, dict):
        raise HTTPException(status_code=400, detail="Profile incomplete: add your skills.")

    missing_docs = []
    for req in ("resume", "photo", "govt_id"):
        if not _has_doc(db, candidate_id, req):
            missing_docs.append(req)
    if missing_docs:
        raise HTTPException(
            status_code=400,
            detail=f"Profile incomplete: upload required documents ({', '.join(missing_docs)})."
        )

    existing_application = db.query(Application).filter(
        Application.candidate_id == candidate_id,
        Application.job_id == job.job_id,
    ).first()
    if existing_application:
        raise HTTPException(status_code=400, detail="Already applied for this job")

    try:
        src_enum = ApplicationSource(payload.source)
    except ValueError:
        raise HTTPException(status_code=422, detail="INVALID_SOURCE")

    employee_id_to_set: Optional[str] = None
    referral_token_to_set: Optional[str] = None

    # if src_enum == ApplicationSource.internal_employee:
    #     if not payload.referral_token:
    #         raise HTTPException(status_code=422, detail="REFERRAL_TOKEN_REQUIRED")
    #
    #     tok = db.query(JobReferralTokenStorage).filter(
    #         JobReferralTokenStorage.referral_token == payload.referral_token
    #     ).first()
    #     if not tok:
    #         raise HTTPException(status_code=404, detail="INVALID_REFERRAL_TOKEN")
    #     if tok.job_id != job_id:
    #         raise HTTPException(status_code=422, detail="REFERRAL_TOKEN_JOB_MISMATCH")
    #     if (tok.candidate_email or "").lower() != (candidate.email or "").lower():
    #         raise HTTPException(status_code=422, detail="REFERRAL_TOKEN_EMAIL_MISMATCH")
    #     already_used = db.query(Application).filter(
    #         Application.referral_token == payload.referral_token
    #     ).first()
    #     if already_used:
    #         raise HTTPException(status_code=400, detail="REFERRAL_TOKEN_ALREADY_USED")
    #
    #     employee_id_to_set = tok.employee_id
    #     referral_token_to_set = tok.referral_token

    new_app_id = generate_application_id(db)
    application = Application(
        application_id=new_app_id,
        candidate_id=candidate_id,
        employee_id=employee_id_to_set,
        job_id=job.job_id,
        source=src_enum,
        # referral_token=referral_token_to_set,
        status="under_review",
        applied_at=datetime.utcnow(),
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    # Queue resume screening
    background_tasks.add_task(_screen_application_bg, application.application_id)

    # Email candidate
    is_referral = (src_enum == ApplicationSource.internal_employee)
    email_sent, email_error = await send_application_confirmation_email(
        receiver_email=candidate.email,
        first_name=candidate.first_name,
        job_id=job.job_id,
        application_id=application.application_id,
        is_referral=is_referral,
        referral_id=referral_token_to_set,
    )

    # ================================
    # In-app notification (DYNAMIC)
    # ================================
    # Send to the HR who posted this job (purely dynamic: no hardcoded IDs)
    hr_employee_id = (job.posted_by_employee_id or "").strip()
    logger.info(f"[apply-job] posted_by_employee_id = {job.posted_by_employee_id}")

    if hr_employee_id:
        hr_employee_id = hr_employee_id.strip()

        notif = NotificationStructure(
            scenario="job_application_submitted",
            meta_data=NotificationMeta(
                routh_path=f"/admin/applications/page?application_id={application.application_id}",
                http_method="GET",
            ),
            message=(
                f"New application: {candidate.first_name} {candidate.last_name} "
                f"applied for {job.title}"
            ),
        )

        background_tasks.add_task(
            _notify_hr_application_sync,
            hr_employee_id,
            notif
        )


    return {
        "message": "Job applied successfully via referral" if is_referral else "Job applied successfully",
        "applied_via": "referral" if is_referral else "external",
        "referral_id": referral_token_to_set,
        "application_id": application.application_id,
        "job_id": job.job_id,
        "candidate_id": candidate_id,
        "first_name": candidate.first_name,
        "last_name": candidate.last_name,
        "email": candidate.email,
        "contact_number": candidate.contact_number,
        "status": application.status,
        "source": (application.source.value if hasattr(application.source, "value") else str(application.source)),
        "employee_id": application.employee_id,
        "email_sent": email_sent,
        "email_error": email_error,
        "screening_queued": True,
    }


# =============================================================================
# Candidate: My applications list
# =============================================================================
@router.get("/applications/me", response_model=List[MyApplicationOut])
def list_my_applications(
        status: Optional[str] = None,
        is_referral: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
        db: Session = Depends(get_db),
        candidate_id: str = Depends(candidate_session_required),
):
    apps_q = (
        db.query(Application)
        .filter(Application.candidate_id == candidate_id)
        .order_by(desc(Application.applied_at))
    )
    if status:
        apps_q = apps_q.filter(Application.status == status)

    if is_referral is True:
        apps_q = apps_q.filter(Application.source == ApplicationSource.internal_employee)
    elif is_referral is False:
        apps_q = apps_q.filter(Application.source != ApplicationSource.internal_employee)

    apps = apps_q.offset(offset).limit(limit).all()

    out: List[MyApplicationOut] = []
    for a in apps:
        j = a.job
        if not j:
            continue
        job_out = MyApplicationJobOut(
            job_id=j.job_id,
            title=j.title,
            location=j.location,
            work_mode=j.work_mode,
            experience=j.total_experience,
            skills=_skills_csv(j),
        )
        out.append(
            MyApplicationOut(
                application_id=a.application_id,
                job=job_out,
                status=a.status,
                applied_at=a.applied_at,
                source=(a.source.value if hasattr(a.source, "value") else str(a.source)),
            )
        )
    return out



