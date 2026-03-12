import os
import re
import logging
import asyncio
import tempfile
import requests
import urllib.parse
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple, Any, Literal
from fastapi import UploadFile, HTTPException, status
from app.utils.aws_email import aws_send_mail

# from pdfminer.high_level import extract_text as extract_pdf_text
from pdfminer.high_level import extract_text as extract_pdf_text

from docx import Document
from sqlalchemy.orm import Session

from app.models.user_models import CandidateProfileInformation, CandidateDocument
from app.models.job_models import Application, Job
from app.models.screening_models import ResumeScreening

# 🔔 NEW: get HR profile + in-app notification schema/sender
from app.models.auth_models import ProfileInformation
from app.schemas.user_schemas import NotificationMeta, NotificationStructure
from app.utils.notify_depends import send_notification_to_employee as _notify_employee_async

logger = logging.getLogger(__name__)

# =================== Optional fuzzy matching ===================
# try:
#     from rapidfuzz import fuzz
#     FUZZY_AVAILABLE = True
# except ImportError:
#     FUZZY_AVAILABLE = False

# =================== Optional .doc extractor (fallback) ===================
try:
    # import textract  # for legacy .doc
    _TEXTRACT_AVAILABLE = True
except Exception:
    _TEXTRACT_AVAILABLE = False

# ============================================================
#  EMAIL CONFIG (AWS SES SMTP)
# ============================================================

SMTP_SERVER = os.getenv("SES_SMTP_SERVER", "email-smtp.us-east-1.amazonaws.com")
SMTP_PORT = int(os.getenv("SES_SMTP_PORT", "587"))

SES_USERNAME = os.getenv("SES_USER")        # SMTP username
SES_PASSWORD = os.getenv("SES_PASS")        # SMTP password

SMTP_SENDER_EMAIL = os.getenv(
    "SMTP_SENDER_EMAIL",
    "careers@securxperts.com"               # must be verified in SES
)


# ============================================================
#  ASYNC EMAIL WRAPPER (USED ACROSS PROJECT)
# ============================================================

async def send_application_email(
    to_emails: List[str],
    subject: str,
    body: str,
    body_type: Literal["plain", "html"] = "plain",
    cc_emails: Optional[List[str]] = None,
    bcc_emails: Optional[List[str]] = None,
    reply_to: Optional[List[str]] = None,
    attachments: Optional[List[UploadFile]] = None,
):
    """
    Unified async email sender using AWS SES SMTP
    """

    try:
        return await aws_send_mail(
            #from_email=SMTP_SENDER_EMAIL,
            reciver_to=to_emails,
            reciver_cc=cc_emails,
            reciver_bcc=bcc_emails,
            reply_to=reply_to,
            subject=subject,
            body=body,
            body_type=body_type,
            attachment_objs=attachments,
            SMTP_SERVER=SMTP_SERVER,
            SMTP_PORT=SMTP_PORT,
            SENDGRID_USERNAME=SES_USERNAME,   # reused param name internally
            SENDGRID_PASSWORD=SES_PASSWORD,   # reused param name internally
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email: {str(e)}"
        )

# =================== S3 download (preserve extension) ===================
# NOTE: We implement our own S3 downloader here to preserve file extensions.
# If you prefer using app.utils.s3_utils, update that util similarly.
def _download_s3_to_tempfile_preserve_ext(s3_uri: str) -> str:
    """
    Download an s3://bucket/key object to a temp file, preserving the
    original file extension so downstream extraction can choose the parser.
    """
    try:
        import boto3
    except Exception as e:
        logger.error(f"boto3 not available for S3 download: {e}")
        return ""

    if not s3_uri.lower().startswith("s3://"):
        logger.error(f"Invalid S3 URI: {s3_uri}")
        return ""

    try:
        path_part = s3_uri[5:]  # strip 's3://'
        bucket, key = path_part.split("/", 1)
        _, ext = os.path.splitext(key)
        fd, tmp_path = tempfile.mkstemp(suffix=ext or ".pdf")
        os.close(fd)
        s3 = boto3.client("s3")
        s3.download_file(bucket, key, tmp_path)
        return tmp_path
    except Exception as e:
        logger.error(f"Failed to download S3 object {s3_uri}: {e}")
        return ""

# =================== HTTP(S) download helper (preserve extension) ===================
def _download_http_to_tempfile(url: str, timeout: int = 20) -> str:
    """
    Download file via HTTP(S) while preserving its extension. This is critical
    because the extractor relies on the file extension to pick the parser.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed.path)  # ignore query params
        _, ext = os.path.splitext(filename)
        fd, path = tempfile.mkstemp(suffix=ext or ".pdf")
        with os.fdopen(fd, "wb") as f:
            resp = requests.get(url, stream=True, timeout=timeout)
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return path
    except Exception as e:
        logger.error(f"HTTP download failed for {url}: {e}")
        return ""

# =================== Local file passthrough (adds extension if missing) ===================
def _ensure_extension_local_path(path: str, fallback_ext: str = ".pdf") -> str:
    """
    Some environments may pass a temp file without an extension.
    If the original URL/Key is available as a query/fragment, we cannot recover here.
    This helper only ensures there's SOME extension to avoid empty-suffix issues.
    """
    _, ext = os.path.splitext(path)
    if ext:
        return path
    # copy to new tmp with suffix so parser can pick it up
    try:
        fd, fixed = tempfile.mkstemp(suffix=fallback_ext)
        with os.fdopen(fd, "wb") as out, open(path, "rb") as inp:
            out.write(inp.read())
        return fixed
    except Exception as e:
        logger.warning(f"Failed to clone local path with suffix: {e}")
        return path

# =================== Resume Text Extraction (PDF + DOCX + DOC + RTF + TXT) ===================
def extract_resume_text(file_path: str) -> str:
    """
    Extract text from a resume file.
    Supports: PDF (.pdf), Word (.docx), legacy Word (.doc via textract if available),
              RTF (.rtf), and TXT (.txt).
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    file_ext = os.path.splitext(file_path)[1].lower().strip()
    text_content = ""

    try:
        if file_ext == ".pdf":
            text_content = extract_pdf_text(file_path) or ""

        elif file_ext == ".docx":
            parts: List[str] = []
            try:
                doc = Document(file_path)
                for para in doc.paragraphs:
                    if para.text.strip():
                        parts.append(para.text.strip())
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            for para in cell.paragraphs:
                                if para.text.strip():
                                    parts.append(para.text.strip())
                text_content = "\n".join(parts)
            except Exception as e:
                logger.error(f"DOCX parse error ({file_path}): {e}")
                text_content = ""

        # elif file_ext == ".doc":
        #     if _TEXTRACT_AVAILABLE:
        #         try:
        #             raw = textract.process(file_path)  # may require antiword/catdoc installed
        #             text_content = raw.decode("utf-8", errors="ignore")
        #         except Exception as e:
        #             logger.error(f"textract failed on .doc ({file_path}): {e}")
        #             text_content = ""
        #     else:
        #         logger.warning("Legacy .doc detected, but textract not available. Returning empty text.")
        #         text_content = ""

        elif file_ext in (".txt",):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text_content = f.read()
            except Exception as e:
                logger.error(f"TXT read failed ({file_path}): {e}")
                text_content = ""

        elif file_ext in (".rtf",):
            # naive RTF read; for higher fidelity use striprtf or textract if needed
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text_content = f.read()
            except Exception as e:
                logger.error(f"RTF read failed ({file_path}): {e}")
                text_content = ""

        else:
            logger.warning(f"Unsupported file type for resume: {file_ext}")
            return ""

        return (text_content or "").strip()

    except Exception as e:
        logger.error(f"Error extracting resume text from {file_path}: {e}")
        return ""

# =================== Normalization / Matching ===================
def normalize_for_words(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def normalize_collapsed(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())

def tokenize(text: str) -> list:
    return re.findall(r"[a-z0-9]+", (text or "").lower())

# ✅ Synonyms / alias expansion
_SKILL_SYNONYMS = {
    "fastapi": {"fast api", "fast-api"},
    "restapi": {"rest api", "rest-api"},
    "javascript": {"js", "javascript", "java script"},
    "typescript": {"ts", "type script"},
    "nodejs": {"node", "node.js", "node js"},
    "postgresql": {"postgres", "postgre sql", "postgre-sql"},
    "sql": {"structured query language", "sequel"},
    "aws": {"amazon web services"},
    "kubernetes": {"k8s"},
    "docker": {"dockerized", "docker compose", "docker-compose", "dockers"},
    "git": {"git scm"},
    "react": {"reactjs", "react js"},
    "vue": {"vuejs", "vue js"},
    "csharp": {"c#", "c sharp"},
    "dotnet": {".net", "dot net"},
    "cicd": {"ci/cd", "ci cd", "continuous integration", "continuous delivery", "continuous deployment"},
}

def skill_variants(skill_raw: str) -> set:
    base_tokens = tokenize(skill_raw)
    collapsed_base = "".join(base_tokens)
    variants = {collapsed_base}
    base_key = collapsed_base
    if base_key in _SKILL_SYNONYMS:
        for alias in _SKILL_SYNONYMS[base_key]:
            variants.add(normalize_collapsed(alias))
    variants.add(normalize_collapsed(skill_raw))
    return {v for v in variants if v}

def contains_skill(resume_words_text: str, resume_collapsed_text: str, skill_raw: str) -> bool:
    for v in skill_variants(skill_raw):
        if v and v in resume_collapsed_text:
            return True
    tokens_ = tokenize(skill_raw)
    if len(tokens_) > 1:
        pattern = r"\b" + r"[^a-z0-9]*".join(map(re.escape, tokens_)) + r"\b"
        if re.search(pattern, resume_words_text, flags=re.IGNORECASE):
            return True
    elif len(tokens_) == 1:
        t = tokens_[0]
        if re.search(rf"\b{re.escape(t)}\b", resume_words_text, flags=re.IGNORECASE):
            return True
    # if FUZZY_AVAILABLE:
    #     for word in tokenize(resume_words_text):
    #         if fuzz.ratio(word, (skill_raw or "").lower().strip()) > 85:
    #             return True
    return False

# =================== Experience helpers ===================
def parse_experience_simple(exp_value) -> float:
    if exp_value is None:
        return 0.0
    if isinstance(exp_value, (int, float)):
        return float(exp_value)
    if isinstance(exp_value, str):
        s = exp_value.strip().lower()
        if any(w in s for w in ["fresher", "intern", "no exp", "no experience", "none", "na", "n/a"]):
            return 0.0
        mrange = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", s)
        if mrange:
            try:
                return float(mrange.group(1))
            except ValueError:
                return 0.0
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return 0.0
    return 0.0

def parse_job_min_experience(exp_field: Optional[str]) -> float:
    if not exp_field:
        return 0.0
    s = exp_field.strip().lower()
    s = s.replace("–", "-").replace("—", "-")
    range_m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", s)
    if range_m:
        try:
            return float(range_m.group(1))
        except ValueError:
            pass
    plus_m = re.search(r"(\d+(?:\.\d+)?)\s*\+\s*years?", s)
    if plus_m:
        try:
            return float(plus_m.group(1))
        except ValueError:
            pass
    return parse_experience_simple(s)

def extract_years_from_resume_text(text: str) -> float:
    if not text:
        return 0.0
    yrs = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:years|yrs|yr)\s+of\s+experience", text.lower()):
        try:
            yrs.append(float(m.group(1)))
        except ValueError:
            pass
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:years|yrs|yr)\b", text.lower()):
        try:
            yrs.append(float(m.group(1)))
        except ValueError:
            pass
    return max(yrs) if yrs else 0.0

def compute_candidate_total_experience_years(cand: CandidateProfileInformation, resume_text: str) -> float:
    tot = parse_experience_simple(getattr(cand, "total_experience", None))
    if tot > 0:
        return tot
    return extract_years_from_resume_text(resume_text)

def compute_candidate_relevant_experience_years(cand: CandidateProfileInformation, resume_text: str, fallback_total: float) -> float:
    rel = parse_experience_simple(getattr(cand, "relevant_experience", None))
    if rel > 0:
        return rel
    return fallback_total

# =================== Skills helpers ===================
def _csv_to_list(csv_text: Optional[str]) -> List[str]:
    if not csv_text:
        return []
    parts = [p.strip().lower() for p in csv_text.split(",") if p.strip()]
    out, seen = [], set()
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out

def _dedupe_preserve_order(items: List[str]) -> List[str]:
    out, seen = [], set()
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out

def job_skill_names(job: Job) -> List[str]:
    prim = _csv_to_list(getattr(job, "primary_skills", None))
    sec = _csv_to_list(getattr(job, "secondary_skills", None))
    return _dedupe_preserve_order(prim + sec)

def candidate_profile_skill_names(cand: CandidateProfileInformation) -> List[str]:
    names: List[str] = []
    if isinstance(cand.skills, dict):
        s = cand.skills or {}
        for key in ("primary_skills", "secondary_skills", "primary", "secondary"):
            val = s.get(key)
            if isinstance(val, list):
                names.extend([str(x).strip().lower() for x in val if str(x).strip()])
            elif isinstance(val, str):
                names.extend(_csv_to_list(val))
    else:
        names.extend(_csv_to_list(str(cand.skills or "")))
    return _dedupe_preserve_order(names)

# =================== Experience scoring vs Job ===================
def _experience_scores(job: Job, cand: CandidateProfileInformation, resume_text: str) -> Tuple[float, float, float, str]:
    req_total_min = parse_job_min_experience(getattr(job, "total_experience", None))
    req_rel_min   = parse_job_min_experience(getattr(job, "relevant_experience", None))

    cand_total_years = compute_candidate_total_experience_years(cand, resume_text)
    cand_rel_years   = compute_candidate_relevant_experience_years(cand, resume_text, cand_total_years)

    parts = []
    match_flags = []

    if req_total_min > 0:
        score_total = min((cand_total_years / req_total_min) * 100.0, 100.0)
        parts.append(score_total)
        match_flags.append(cand_total_years >= req_total_min)

    if req_rel_min > 0:
        score_rel = min((cand_rel_years / req_rel_min) * 100.0, 100.0)
        parts.append(score_rel)
        match_flags.append(cand_rel_years >= req_rel_min)

    if not parts:
        return 100.0, cand_total_years, 0.0, "Yes"

    final_exp_score = sum(parts) / len(parts)
    exp_match_flag = "Yes" if all(match_flags) else "No"
    req_used = max(req_total_min, req_rel_min)
    return final_exp_score, cand_total_years, req_used, exp_match_flag

# =================== Email helpers ===================
def _send_email_to_list(to_list: List[str], subject: str, body: str) -> None:
    if not to_list:
        logger.warning("No recipients provided; skipping email.")
        return
    try:
        asyncio.run(send_application_email(
            #from_email=SMTP_SENDER_EMAIL,
            to_emails=to_list,
            subject=subject,
            body=body,
            body_type='plain',
        ))
        logger.info(f"Screening email sent | to={to_list} | subject={subject}")
    except Exception as e:
        logger.error(f"Failed to send screening email: {e}")

def _dedupe_emails(emails: List[str]) -> List[str]:
    seen = set()
    out = []
    for e in emails:
        if not e:
            continue
        key = e.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(e.strip())
    return out

def _emails_from_profile_email_field(email_field: Any) -> List[str]:
    emails: List[str] = []
    try:
        if isinstance(email_field, str):
            parts = [p.strip() for p in email_field.split(",") if p.strip()]
            emails.extend(parts)
        elif isinstance(email_field, list):
            for v in email_field:
                if isinstance(v, str) and v.strip():
                    emails.append(v.strip())
                elif isinstance(v, dict):
                    for val in v.values():
                        if isinstance(val, str) and "@" in val:
                            emails.append(val.strip())
        elif isinstance(email_field, dict):
            for val in email_field.values():
                if isinstance(val, str) and "@" in val:
                    emails.append(val.strip())
                elif isinstance(val, list):
                    for v in val:
                        if isinstance(v, str) and "@" in v:
                            emails.append(v.strip())
    except Exception:
        pass
    return _dedupe_emails(emails)

def _hr_recipients_for_job(db: Session, job: Job) -> List[str]:
    """Return the posting HR's email(s) dynamically from ProfileInformation.email — no fallback."""
    recipients: List[str] = []
    try:
        if job and getattr(job, "posted_by_employee_id", None):
            prof = (
                db.query(ProfileInformation)
                .filter(ProfileInformation.employee_id == job.posted_by_employee_id)
                .first()
            )
            if prof:
                recipients.extend(
                    _emails_from_profile_email_field(getattr(prof, "email", None))
                )
    except Exception as e:
        logger.error(f"Failed to resolve HR profile emails: {e}")

    recipients = [r for r in recipients if "@" in r]
    recipients = _dedupe_emails(recipients)

    if not recipients:
        logger.warning(
            f"No HR email found for job {getattr(job, 'job_id', 'N/A')} "
            f"posted by {getattr(job, 'posted_by_employee_id', 'Unknown')}"
        )
    return recipients


def _notify_hr_screening_sync(employee_id: Optional[str], notif: NotificationStructure) -> None:
    if not employee_id:
        return

    from app.database import SessionLocal
    import anyio

    db = SessionLocal()
    try:
        anyio.run(
            _notify_employee_async,
            employee_id,   # employee_id
            notif,         # NotificationStructure object (NOT dict)
            db             # db session (MANDATORY)
        )
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(
            f"Failed to notify HR for screening {employee_id}: {e}",
            exc_info=True
        )
    finally:
        db.close()


# =================== Core Screening ===================
def screen_resume(db: Session, application_id: str) -> Dict[str, Any]:
    try:
        application: Optional[Application] = db.query(Application).filter_by(
            application_id=application_id
        ).first()
        if not application:
            return {"error": "Application not found"}

        candidate: Optional[CandidateProfileInformation] = application.candidate
        job: Optional[Job] = application.job
        if not candidate or not job:
            return {"error": "Candidate or Job not found"}

        # Most recent resume via CandidateDocument
        resume_row: Optional[CandidateDocument] = (
            db.query(CandidateDocument)
            .filter(
                CandidateDocument.candidate_id == candidate.candidate_id,
                CandidateDocument.doc_name == "resume",
            )
            .order_by(CandidateDocument.uploaded_at.desc())
            .first()
        )
        if not resume_row:
            return {"error": "Candidate resume not uploaded"}

        original_path = resume_row.file_path or ""
        local_resume_path = original_path
        temp_to_cleanup: Optional[str] = None

        try:
            # Preserve extension
            if original_path.lower().startswith("s3://"):
                local_resume_path = _download_s3_to_tempfile_preserve_ext(original_path)
                temp_to_cleanup = local_resume_path
            elif original_path.lower().startswith("http://") or original_path.lower().startswith("https://"):
                local_resume_path = _download_http_to_tempfile(original_path)
                temp_to_cleanup = local_resume_path
            else:
                local_resume_path = _ensure_extension_local_path(original_path, fallback_ext=".pdf")

            if not local_resume_path or not os.path.exists(local_resume_path):
                return {"error": f"Resume file not found at {original_path}"}

            raw_resume_text = extract_resume_text(local_resume_path)
            if not raw_resume_text.strip():
                return {"error": "Resume text could not be extracted"}

            # -------- Preprocess resume text --------
            resume_words_text = normalize_for_words(raw_resume_text)
            resume_collapsed_text = normalize_collapsed(raw_resume_text)

            # -------- Skills Matching --------
            job_skills_raw = job_skill_names(job)
            job_skills_set = {s.lower().strip() for s in job_skills_raw}

            matched_from_resume = set()
            matched_from_profile = set()

            cand_skill_names = candidate_profile_skill_names(candidate)

            for skill in job_skills_raw:
                s_norm = skill.lower().strip()
                if contains_skill(resume_words_text, resume_collapsed_text, s_norm):
                    matched_from_resume.add(s_norm)
                job_variants = skill_variants(s_norm)
                for cand_skill in cand_skill_names:
                    if normalize_collapsed(cand_skill) in job_variants:
                        matched_from_profile.add(s_norm)
                        break

            matched_total = matched_from_resume | matched_from_profile
            missing_skills = job_skills_set - matched_total
            skills_score = (len(matched_total) / len(job_skills_set) * 100.0) if job_skills_set else 100.0

            # -------- Experience Matching --------
            exp_score, cand_years, req_years_used, exp_match_flag = _experience_scores(
                job, candidate, raw_resume_text
            )

            # -------- Final Score & Status --------
            final_score = (skills_score + exp_score) / 2.0 if job_skills_set else exp_score

            # ❗ ONLY screening_status (NO app_status)
            if final_score > 55:
                screening_status = "Shortlisted"
            elif 45 <= final_score <= 55:
                screening_status = "On Hold"
            else:
                screening_status = "Rejected"

            # -------- ResumeScreening Row --------
            screening = db.query(ResumeScreening).filter_by(
                application_id=application.application_id
            ).first()

            def _cap(s: str, n: int = 500) -> str:
                return (s or "")[:n]

            matched_str = _cap(",".join(sorted(matched_total)))
            missing_str = _cap(",".join(sorted(missing_skills)))
            previous_screening_status = getattr(screening, "status", None) if screening else None

            # Update or create screening record
            if screening:
                screening.matched_skills = matched_str
                screening.missing_skills = missing_str
                screening.experience_match = exp_match_flag
                screening.status = screening_status
                screening.score = final_score
                screening.updated_at = datetime.utcnow()
            else:
                screening = ResumeScreening(
                    application_id=application.application_id,
                    candidate_id=candidate.candidate_id,
                    job_id=job.job_id,
                    matched_skills=matched_str,
                    missing_skills=missing_str,
                    experience_match=exp_match_flag,
                    status=screening_status,
                    score=final_score,
                    created_at=datetime.utcnow()
                )
                db.add(screening)

            db.commit()
            db.refresh(screening)

            # -------- Notify HR Only --------
            # -------- Notify HR Only --------
            try:
                if previous_screening_status != screening_status or previous_screening_status is None:
                    subject = f"[Screening] {screening_status} — App {application.application_id} — {job.title}"
                    body = (
                        f"Application ID: {application.application_id}\n"
                        f"Job: {job.job_id} — {job.title}\n"
                        f"Candidate: {candidate.first_name} {candidate.last_name}\n"
                        f"Experience: {cand_years:.1f}y vs req {req_years_used:.1f}y (match: {exp_match_flag})\n"
                        f"Skills matched: {matched_str or '—'}\n"
                        f"Skills missing: {missing_str or '—'}\n"
                        f"Final Score: {final_score:.1f}%\n"
                        f"Screening Status: {screening_status}\n"
                        f"Generated at (UTC): {datetime.utcnow():%Y-%m-%d %H:%M:%S}\n"
                    )
                    recipients = _hr_recipients_for_job(db, job)
                    _send_email_to_list(recipients, subject, body)

                    notif = NotificationStructure(
                        scenario="resume_screened",
                        meta_data=NotificationMeta(
                            routh_path=(
                                f"/admin/applications/"
                                f"{application.application_id}/screening-basic"
                            ),
                            http_method="GET",
                        ),
                        message=(
                            f"Screening {screening_status}: {candidate.first_name} {candidate.last_name} "
                            f"→ {job.title} ({job.job_id}) | Final Score: {final_score:.1f}%"
                        ),
                    )

                    _notify_hr_screening_sync(
                        getattr(job, "posted_by_employee_id", None),
                        notif
                    )

            except Exception as e:
                logger.error(f"Failed to notify HR for screening {application.application_id}: {e}")


            return {
                "application_id": application.application_id,
                "candidate": f"{candidate.first_name} {candidate.last_name}",
                "job": job.title,
                "resume_path": original_path,
                "matched_skills": sorted(list(matched_total)),
                "missing_skills": sorted(list(missing_skills)),
                "skills_score": round(skills_score, 1),
                "experience_score": round(exp_score, 1),
                "final_score": round(final_score, 1),
                "screening_status": screening_status,
                "hr_notified_employee_id": getattr(job, "posted_by_employee_id", None),
            }

        finally:
            try:
                if temp_to_cleanup and os.path.exists(temp_to_cleanup):
                    os.remove(temp_to_cleanup)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"screen_resume failed for application {application_id}: {e}")
        return {"error": str(e)}


# =================== Batch Screening ===================
def screen_multiple_applications(db: Session, application_ids: List[str]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for app_id in application_ids:
        try:
            res = screen_resume(db, app_id)
            results.append(res)
        except Exception as e:
            logger.error(f"Batch screening failed for {app_id}: {e}")
            results.append({"application_id": app_id, "error": str(e), "status": "error"})
    return results





