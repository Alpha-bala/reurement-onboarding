# app/utils/s3_utils.py
import os
import boto3
import tempfile
from urllib.parse import urlparse, quote
from botocore.exceptions import NoCredentialsError, ClientError


# =========================
# Env helpers
# =========================
def _region() -> str:
    return os.getenv("AWS_REGION", "us-east-1").strip()


def _bucket_name() -> str:
    return os.getenv("AWS_S3_BUCKET", "hrmssg").strip()


def _cloudfront_domain() -> str:
    # e.g. d123abcde.cloudfront.net or files.yourdomain.com
    return (os.getenv("CLOUDFRONT_DOMAIN") or "").strip().strip("/")


def _file_url_mode() -> str:
    """
    URL modes:
      - cloudfront_public → build https://<CLOUDFRONT_DOMAIN>/<key>
      - s3_public         → build https://<bucket>.s3.<region>.amazonaws.com/<key>
      - s3_presigned      → generate presigned S3 GET URL

    Defaults intelligently:
      - If CLOUDFRONT_DOMAIN is set → 'cloudfront_public'
      - Else → 's3_presigned' (safe fallback; won't require public bucket)
    """
    explicit = (os.getenv("FILE_URL_MODE") or "").strip().lower()
    if explicit in {"cloudfront_public", "s3_public", "s3_presigned"}:
        return explicit
    return "cloudfront_public" if _cloudfront_domain() else "s3_presigned"


def _public_acl_enabled() -> bool:
    """
    When using s3_public mode, you can opt-in to setting ACL=public-read on upload.
    If your bucket policy already grants public read, you can leave this False.
    """
    return (os.getenv("AWS_S3_SET_PUBLIC_ACL", "false").strip().lower() in {"1", "true", "yes"})


# =========================
# Boto3 client
# =========================
def _get_s3():
    # Read env at call time (after load_dotenv has run)
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        raise RuntimeError(
            "AWS credentials not found in environment. "
            "Check .env formatting and that load_dotenv() ran."
        )

    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=_region(),
    )


# =========================
# Core operations
# =========================
def _upload_to_s3(
        file_bytes: bytes,
        key: str,
        content_type: str = "application/octet-stream",
        cache_seconds: int = 31536000,  # 1 year
) -> str:
    """
    Uploads bytes to S3 at <bucket>/<key>.
    Returns an internal canonical URI: s3://bucket/key
    (Use generate_http_url(...) to convert to a browser URL based on your mode.)
    """
    try:
        extra = {
            "Bucket": _bucket_name(),
            "Key": key,
            "Body": file_bytes,
            "ContentType": content_type,
            "CacheControl": f"public, max-age={int(cache_seconds)}",
            # Inline so images/PDFs render in browser tabs by default.
            "ContentDisposition": "inline",
        }

        # Only add ACL when explicitly using s3_public and you asked for it.
        if _file_url_mode() == "s3_public" and _public_acl_enabled():
            extra["ACL"] = "public-read"

        s3 = _get_s3()
        s3.put_object(**extra)
        return f"s3://{_bucket_name()}/{key}"
    except (NoCredentialsError, ClientError) as e:
        raise RuntimeError(f"S3 upload failed: {e}")


def _delete_s3_if_uri(s3_uri: str):
    if not s3_uri or not s3_uri.startswith("s3://"):
        return
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        s3 = _get_s3()
        s3.delete_object(Bucket=bucket, Key=key)
    except ClientError as e:
        raise RuntimeError(f"S3 delete failed: {e}")


def _download_s3_to_tempfile(s3_uri: str) -> str:
    """
    Download s3://bucket/key to a local temp file and return the path.
    """
    if not s3_uri.startswith("s3://"):
        raise ValueError("Not an s3:// URI")
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        s3 = _get_s3()
        tmp = tempfile.NamedTemporaryFile(delete=False)
        s3.download_fileobj(bucket, key, tmp)
        tmp.close()
        return tmp.name
    except ClientError as e:
        raise RuntimeError(f"S3 download failed: {e}")


# =========================
# URL generation
# =========================
def _encode_key_for_url(key: str) -> str:
    """
    URL-encode the S3 key safely for use in browser URLs.
    """
    # quote() leaves '/' intact when safe='/' so path structure remains.
    return quote(key, safe='/')


def generate_presigned_url(s3_uri: str, expires_in: int = 3600) -> str:
    """
    Returns a time-limited presigned S3 GET URL for s3:// URIs.
    If a non-s3 URL is passed, it is returned unchanged.
    """
    if not s3_uri.startswith("s3://"):
        return s3_uri
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        s3 = _get_s3()
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=int(expires_in),
        )
    except ClientError as e:
        raise RuntimeError(f"Presigned URL generation failed: {e}")


def _s3_virtual_hosted_url(bucket: str, key: str) -> str:
    # Virtual-hosted–style URL
    # https://bucket.s3.<region>.amazonaws.com/key
    return f"https://{bucket}.s3.{_region()}.amazonaws.com/{_encode_key_for_url(key)}"


def _cloudfront_url(key: str) -> str:
    domain = _cloudfront_domain()
    if not domain:
        raise RuntimeError(
            "CLOUDFRONT_DOMAIN is not set but FILE_URL_MODE=cloudfront_public was chosen."
        )
    return f"https://{domain}/{_encode_key_for_url(key)}"


def generate_http_url(s3_uri: str, expires_in: int = 3600) -> str:
    """
    Convert a canonical s3://bucket/key URI into a browser-openable URL
    according to FILE_URL_MODE.

    Modes:
      - cloudfront_public → https://<CLOUDFRONT_DOMAIN>/<key> (permanent)
      - s3_public         → https://<bucket>.s3.<region>.amazonaws.com/<key> (permanent)
      - s3_presigned      → presigned https URL (expires in `expires_in`)

    If `s3_uri` is already http(s), it is returned unchanged.
    """
    if not s3_uri:
        return s3_uri
    if s3_uri.startswith("http://") or s3_uri.startswith("https://"):
        return s3_uri
    if not s3_uri.startswith("s3://"):
        # unknown scheme → return as-is
        return s3_uri

    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    mode = _file_url_mode()

    if mode == "cloudfront_public":
        # Keep bucket private; CloudFront (preferably with OAC) serves public content.
        return _cloudfront_url(key)
    elif mode == "s3_public":
        # Object must be publicly readable (via ACL or bucket policy).
        return _s3_virtual_hosted_url(bucket, key)
    else:
        # s3_presigned
        return generate_presigned_url(s3_uri, expires_in=expires_in)

