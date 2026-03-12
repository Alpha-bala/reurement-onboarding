import io
import secrets
import qrcode
import boto3
import io
import secrets
import qrcode
import boto3
import os
import logging
from dotenv import load_dotenv
from pathlib import Path
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET")
BASE_URL = os.getenv("BASE_URL")


def generate_qr_with_token(token=None, base_url=None, s3_folder="qrcodes"):
    """
    Generate a QR code for offer verification and upload it to S3.

    Args:
        token (str, optional): Unique token for the QR code. If None, generates a random token.
        base_url (str, optional): Base URL for verification. Uses BASE_URL env var if None.
        s3_folder (str, optional): S3 folder to store the QR code.

    Returns:
        tuple: (token, qr_s3_url, s3_key)
    """
    try:
        # Use environment variable BASE_URL if base_url is not provided
        base_url = base_url or BASE_URL
        if not base_url:
            raise ValueError("base_url or BASE_URL environment variable must be provided")

        # Generate token if not provided
        token = token or secrets.token_urlsafe(16)
        logger.info(f"Generated token: {token}")

        # Full verification URL encoded in the QR
        verify_url = f"{base_url}/{token}"
        logger.info(f"QR will encode URL: {verify_url}")

        # Generate QR code in memory
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(verify_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        # Save QR to BytesIO
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        # S3 key for QR code
        s3_key = f"{s3_folder}/{token}.png"

        # Upload to S3
        s3_client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY
        )
        s3_client.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=s3_key,
            Body=buffer,
            ContentType="image/png"
        )

        # Public URL of QR in S3
        qr_s3_url = f"https://{AWS_S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        logger.info(f"QR uploaded to S3: {qr_s3_url}")

        return token, qr_s3_url, s3_key
    except Exception as e:
        logger.error(f"Failed to generate QR code: {e}", exc_info=True)
        raise

