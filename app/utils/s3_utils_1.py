# app/utils/s3_utils_1.py

from __future__ import annotations

import os
import uuid
import mimetypes
from typing import Optional, BinaryIO
from fastapi import UploadFile
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
load_dotenv()

# ------------------ Env & Client ------------------ #
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

# Try all common bucket envs (both codepaths supported)
S3_BUCKET = (
    os.getenv("S3_BUCKET_NAME")
    or os.getenv("S3_BUCKET")
    or os.getenv("BUCKET_NAME")
    or ""
)

# Optional server-side encryption settings (1st-person API)
S3_SSE = os.getenv("S3_SSE", "")           # e.g. "AES256" or "aws:kms"
S3_KMS_KEY_ID = os.getenv("S3_KMS_KEY_ID", "")

# Default prefixes for 1st-person API
S3_CERTS_PREFIX = os.getenv("S3_CERTS_PREFIX", "certificates")
S3_ASSETS_PREFIX = os.getenv("S3_ASSETS_PREFIX", "courses")

_s3 = boto3.client("s3", region_name=AWS_REGION)


def _ensure_bucket():
    if not S3_BUCKET:
        raise RuntimeError("S3 bucket not configured (set S3_BUCKET_NAME / S3_BUCKET / BUCKET_NAME)")


# ------------------ Utility helpers (shared) ------------------ #

def _guess_content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    ctype, _ = mimetypes.guess_type(filename or "")
    return ctype or fallback


def _public_url(key: str) -> str:
    """
    Region-safe virtual-hosted URL:
      https://{bucket}.s3.{region}.amazonaws.com/{key}
    """
    key = key.lstrip("/")
    return f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"


def url_to_key(url: str) -> str:
    """
    Convert a public S3 URL back into an S3 key.
    Handles both regional and global styles.
    """
    url = (url or "").strip()
    if not url:
        return url

    regional = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/"
    if url.startswith(regional):
        return url[len(regional):]

    global_ = f"https://{S3_BUCKET}.s3.amazonaws.com/"
    if url.startswith(global_):
        return url[len(global_):]

    # Best-effort fallback
    if ".amazonaws.com/" in url:
        return url.split(".amazonaws.com/", 1)[-1]
    return url


# ------------------ 1) "1st person S3 storage" API ------------------ #
# (Keep behavior the same; returns s3:// URIs; works with raw bytes/fileobj)

def key_for_certificate(company_id: int | str, employee_id: str, course_id: int | str, filename: str) -> str:
    """
    Build an S3 key for certificates.
    Example: certificates/<company>/<employee>/<course>/<file>
    """
    return f"{S3_CERTS_PREFIX}/{company_id}/{employee_id}/{course_id}/{filename}".replace("\\", "/")


def key_for_course_asset(course_id: int | str, filename: str) -> str:
    """
    Build an S3 key for course assets.
    Example: courses/<course_id>/<filename>
    """
    return f"{S3_ASSETS_PREFIX}/{course_id}/{filename}".replace("\\", "/")


def put_bytes(data: bytes, key: str, *, content_type: str) -> str:
    """
    Upload raw bytes to S3.
    Returns s3:// URI.
    """
    _ensure_bucket()
    kwargs = {
        "Bucket": S3_BUCKET,
        "Key": key,
        "Body": data,
        "ContentType": content_type,
    }
    if S3_SSE:
        kwargs["ServerSideEncryption"] = S3_SSE
        if S3_SSE == "aws:kms" and S3_KMS_KEY_ID:
            kwargs["SSEKMSKeyId"] = S3_KMS_KEY_ID

    _s3.put_object(**kwargs)
    return f"s3://{S3_BUCKET}/{key}"


def put_fileobj(fileobj: BinaryIO, key: str, *, content_type: str) -> str:
    """
    Upload a file-like object to S3.
    Returns s3:// URI.
    """
    _ensure_bucket()
    extra = {"ContentType": content_type}
    if S3_SSE:
        extra["ServerSideEncryption"] = S3_SSE
        if S3_SSE == "aws:kms" and S3_KMS_KEY_ID:
            extra["SSEKMSKeyId"] = S3_KMS_KEY_ID

    _s3.upload_fileobj(Fileobj=fileobj, Bucket=S3_BUCKET, Key=key, ExtraArgs=extra)
    return f"s3://{S3_BUCKET}/{key}"


def download_file(key: str, local_path: str) -> str:
    """
    Download a file from S3 to a local path.
    Returns the local path.
    """
    _ensure_bucket()
    _s3.download_file(Bucket=S3_BUCKET, Key=key, Filename=local_path)
    return local_path


def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.startswith("s3://")


def s3_key_from_uri(uri: str) -> str:
    """
    Convert a full s3:// URI into just the key.
    Example: s3://bucket/certificates/file.pdf -> certificates/file.pdf
    """
    return (uri or "").split("/", 3)[-1]


def presigned_url(key_or_url: str, expires_in: int = 3600) -> Optional[str]:
    """
    Generate a presigned URL for temporary access.
    Accepts either a raw S3 *key* (1st-person API) or a full HTTPS URL (your API).
    """
    _ensure_bucket()
    if not key_or_url:
        return None

    key = key_or_url
    if isinstance(key_or_url, str) and key_or_url.startswith("http"):
        key = url_to_key(key_or_url)

    try:
        return _s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": key.lstrip("/")},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError):
        return None


# ------------------ 2) "Your S3 storage" API ------------------ #
# (Keep behavior the same; UploadFile input; returns PUBLIC HTTPS URLs)

def upload_to_s3(file, folder: str = "uploads") -> str:
    """
    Upload FastAPI UploadFile (images/docs/etc.) to:
      {folder}/{uuid}_{original_name}
    Returns PUBLIC HTTPS URL.
    """
    # if UploadFile is None or not isinstance(file, UploadFile):  # type: ignore
    #     raise RuntimeError("upload_to_s3 expects a FastAPI UploadFile")

    if not file or not hasattr(file, "file") or not hasattr(file, "filename"):
        raise RuntimeError("upload_to_s3 expects a FastAPI UploadFile-like object")

    _ensure_bucket()
    unique_name = f"{folder.strip('/')}/{uuid.uuid4()}_{file.filename}"
    content_type = getattr(file, "content_type", None) or _guess_content_type(getattr(file, "filename", ""))

    try:
        file.file.seek(0)  # in case it was read before
    except Exception:
        pass

    _s3.upload_fileobj(
        Fileobj=file.file,
        Bucket=S3_BUCKET,
        Key=unique_name,
        ExtraArgs={"ContentType": content_type},
    )
    return _public_url(unique_name)


def upload_pdf_to_s3(pdf_bytes_io, filename: str) -> str:
    """
    Upload PDF bytes to prescriptions/{uuid}_{filename}
    Returns PUBLIC HTTPS URL.
    """
    _ensure_bucket()
    key = f"prescriptions/{uuid.uuid4()}_{filename}"
    try:
        pdf_bytes_io.seek(0)
    except Exception:
        pass

    _s3.upload_fileobj(
        Fileobj=pdf_bytes_io,
        Bucket=S3_BUCKET,
        Key=key,
        ExtraArgs={"ContentType": "application/pdf"},
    )
    return _public_url(key)


def upload_file_to_s3_path(file: "UploadFile", folder: str = "uploads") -> str:
    """
    Alias maintained for backward compatibility with earlier call sites.
    """
    return upload_to_s3(file, folder=folder)





