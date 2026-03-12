from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import smtplib
from fastapi import UploadFile, HTTPException, status
from typing import List, Optional, Literal
from dotenv import load_dotenv
load_dotenv()
import os

async def aws_send_mail(
        from_email: str = "careers@securxperts.com",  # must be a verified sender in aws_ses
        reciver_to: Optional[List[str]] = None,
        reciver_cc: Optional[List[str]] = None,
        reciver_bcc: Optional[List[str]] = None,
        reply_to: Optional[List[str]] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        body_type: Literal["plain", "html"] = 'plain',
        attachment_objs: List[UploadFile] | None = None,
        filenames: Optional[List[str]] = None,
        MIMEBase_maintype: str = 'application',
        MIMEBase_subtype: str = 'octet-stream',
        SENDGRID_USERNAME: str = os.getenv("SES_USER"),
        SENDGRID_PASSWORD: str = os.getenv("SES_PASS"),
        SMTP_SERVER: str = "email-smtp.us-east-1.amazonaws.com",
        SMTP_PORT: int = 587,
):
    """Send email via SendGrid SMTP without link tracking."""

    try:
        #Validate recipients
        if not (reciver_to or reciver_cc or reciver_bcc):
            raise ValueError("At least one recipient (To, CC, or BCC) is required.")

        # Create multipart object for mail
        msg = MIMEMultipart()
        recivers_list = []

        # Set headers
        if from_email:
            msg["From"] = from_email
        if reciver_to:
            msg["To"] = ", ".join(reciver_to)
            recivers_list.extend(reciver_to)
        if reciver_cc:
            msg["Cc"] = ", ".join(reciver_cc)
            recivers_list.extend(reciver_cc)
        if reciver_bcc:
            msg["Bcc"] = ", ".join(reciver_bcc)
            recivers_list.extend(reciver_bcc)
        if reply_to:
            msg["Reply-To"] = ", ".join(reply_to)


        if subject:
            msg["Subject"] = subject

        # Attach Body
        if body:
            msg.attach(MIMEText(body, body_type))

        # ---- Attachments ----
        if attachment_objs:
            if not filenames:
                filenames = [obj.filename for obj in attachment_objs]
            for attachment_obj, filename in zip(attachment_objs, filenames):
                payload = MIMEBase(MIMEBase_maintype, MIMEBase_subtype)
                payload.set_payload(await attachment_obj.read())
                encoders.encode_base64(payload)
                payload.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                msg.attach(payload)

        # ---- Send ----
        with smtplib.SMTP(host=SMTP_SERVER, port=SMTP_PORT) as server:
            server.starttls()
            server.login(SENDGRID_USERNAME, SENDGRID_PASSWORD)
            server.sendmail(from_email, recivers_list, msg.as_string())

        return {
            "from_email": from_email,
            "reciver_to": reciver_to,
            "reciver_cc": reciver_cc,
            "reciver_bcc": reciver_bcc,
            "recivers_list": recivers_list,
            "reply_to": reply_to,
        }

    except smtplib.SMTPResponseException as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error while sending email: {e}"
        )









