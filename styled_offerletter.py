import os
from io import BytesIO
from PyPDF2 import PdfReader, PdfWriter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import boto3
import io
from app.utils.configure import AWS_ACCESS_KEY_ID, AWS_REGION, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET
from reportlab.lib.utils import ImageReader
from dotenv import load_dotenv

load_dotenv()

def generate_styled_offer_letter(data: dict, template_path: str) -> str:
    """
    Generate a styled offer letter by overlaying dynamic content on a template PDF.
    """

    # === Step 1: Memory buffer instead of direct file ===
    show_salary_breakup = bool(data.get("show_salary_breakup", False))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=0.5 * inch,
        rightMargin=1 * inch,
        topMargin=1.8 * inch,
        bottomMargin=1 * inch,
    )
    story = []

    # === Step 2: Styles ===
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        name='Header', fontSize=10, fontName='Helvetica',
        leading=12, alignment=0, textColor=colors.black, spaceAfter=6
    )
    title_style = ParagraphStyle(
        name='Title', fontSize=13, fontName='Helvetica-Bold',
        alignment=1, spaceAfter=12
    )
    normal_style = ParagraphStyle(
        name='Normal', fontSize=11, fontName='Helvetica',
        leading=14, spaceAfter=8
    )
    section_style = ParagraphStyle(
        name='Section', fontSize=11, fontName='Helvetica-Bold',
        spaceBefore=8, spaceAfter=5
    )
    list_style = ParagraphStyle(
        name='List', fontSize=11, fontName='Helvetica',
        leftIndent=20, spaceAfter=4, bulletIndent=10
    )

    # === Step 3: Header/Footer callback (kept same) ===
    def draw_header_footer(canvas, doc, data=data):
        canvas.saveState()
        border_left = 20
        border_right = A4[0] - 20
        border_top = A4[1] - 20
        available_width = border_right - border_left

        if canvas.getPageNumber() == 1:
            s3_client = boto3.client(
                "s3",
                region_name=AWS_REGION,
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY
            )
            qr_s3_url = data.get('qr_path')
            if not qr_s3_url:
                logger.error("QR path not provided in data")
                return

            try:
                # Extract S3 key from URL
                qr_key = qr_s3_url.split(f"https://{AWS_S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/")[1]

                # Fetch QR from S3
                qr_response = s3_client.get_object(Bucket=AWS_S3_BUCKET, Key=qr_key)
                qr_bytes = qr_response['Body'].read()

                # Convert to ImageReader
                qr_reader = ImageReader(BytesIO(qr_bytes))

                qr_size = 80
                canvas.drawImage(
                    qr_reader,  # correct
                    A4[0] - qr_size - 40,
                    A4[1] - qr_size - 90,
                    width=qr_size,
                    height=qr_size,
                    preserveAspectRatio=True,
                    mask='auto'
                )
            except Exception as e:
                logger.error(f"Failed to fetch QR code from S3: {e}", exc_info=True)

        # Footer
        canvas.setFont("Helvetica", 10)
        canvas.drawCentredString(A4[0] / 2, 0.5 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    # === Step 4: Build Story ===
    story.append(Spacer(1, 0.1 * inch))
    date_ref = (
        f"Offer_id:{data['offer_id']}<br/>"
        f"Date: {data['offer_date']}<br/>"
        f"Ref: HR/Talent Acquisition (TA)/{data['reference_number']}"
    )
    story.append(Paragraph(date_ref, normal_style))
    story.append(Spacer(0, 0.13 * inch))

    story.append(Paragraph(data['name'].upper(), section_style))
    story.append(Paragraph(data['address'].replace("\n", "<br/>"), normal_style))
    story.append(Paragraph(data['phone_number'], normal_style))
    story.append(Paragraph(data['email'], normal_style))
    story.append(Spacer(0, 0.3 * inch))

    story.append(Paragraph("<u>Employment Agreement</u>", title_style))

    story.append(Spacer(0, 0.1 * inch))

    story.append(Paragraph(f"Dear <b>{data['name'].upper()}</b>,", normal_style))
    story.append(Paragraph(
        f"We are pleased to offer you employment with <b>{data['office_name']}</b> "
        f"as <b>{data['position']}</b>.", normal_style
    ))
    story.append(Paragraph(
        "This offer is based on your qualifications and professional background. "
        "Below are the terms and conditions of your employment:", normal_style
    ))
    body_content = [
        ("1. APPOINTMENT DETAILS", [
            f"1.1 Your designation will be <b>{data['position']}</b> and you will be reporting to the <b>{data['reporting_person']}</b> (Contact: <b>{data['reporting_contact_number']}</b>).",
            f"1.2 Your employment will commence on <b>{data['joining_date']}</b> (Joining Date).",
            f"1.3 Reporting time on your joining day will be {data['reporting_time']} at our office: <b>{data['reporting_office_address']}</b>."
        ]),
        ("2. PLACE OF WORK", [
            f"2.1 Your initial place of work will be {data['office_location']}, but you may be required to work at other locations as per business needs.",
            "2.2 Remote work or flexible location arrangements may be applicable depending on company policies."
        ]),
        ("3. COMPENSATION & BENEFITS", [
            # f"3.1 Salary Breakup: {data['annual_ctc']} Annual salary (detailed breakup below).",
            f"3.1 Salary: {data['annual_ctc']} Annual salary." + (
                " Detailed breakup below." if show_salary_breakup else ""),
            "3.2 Deductions: Statutory deductions such as Provident Fund (PF), Professional Tax (PT), and Income Tax (TDS) will be applicable as per government regulations.",
            "3.3 Performance Review: Salary increments, bonuses, or incentives will be subject to periodic performance reviews."
        ])
    ]

    # story.append(Paragraph(body_content, normal_style))
    for title, lines in body_content:
        story.append(Paragraph(title, section_style))
        for line in lines:
            story.append(Paragraph(line, list_style))
        story.append(Spacer(1, 0.1 * inch))
    # story.append(Spacer(1, 0.2 * inch))

    # === PAGE 2: Salary Table ===
    story.append(Paragraph(
        f"Name: {data['name']}     Date: {data['offer_date']}     Location:{data['office_location']}     Band:{data['band']} ",
        section_style))
    story.append(Spacer(1, 0.3 * inch))

    # ==========================================================
    # SALARY BREAKUP TABLE (ONLY IF ENABLED)
    # ==========================================================
    if show_salary_breakup:
        combined_table_data = [
            ["Component", "Annual", "Monthly"],
            ["EARNINGS", "", ""],

            ["Basic Salary", data.get("basic_annual", ""), data.get("basic_monthly", "")],
            ["House Rent Allowance (HRA)", data.get("hra_annual", ""), data.get("hra_monthly", "")],
            ["Food Allowance", data.get("food_annual", ""), data.get("food_monthly", "")],
            ["Special Allowance", data.get("special_annual", ""), data.get("special_monthly", "")],
            ["Other Allowance", data.get("other_annual", ""), data.get("other_monthly", "")],

            ["Total CTC", data.get("annual_ctc", ""), data.get("monthly_ctc", "")],

            ["DEDUCTIONS", "", ""],

            ["Provident Fund", data.get("pf_annual", ""), data.get("pf_monthly", "")],
            ["Professional Tax", data.get("pt_annual", ""), data.get("pt_monthly", "")],
            ["TDS", data.get("tds_annual", ""), data.get("tds_monthly", "")],
            ["Health Insurance", data.get("health_annual", ""), data.get("health_monthly", "")],

            ["Net Salary Post Tax", data.get("net_annual", ""), data.get("net_monthly", "")],
        ]

        left_margin = 1 * inch
        right_margin = 1 * inch
        available_width = A4[0] - left_margin - right_margin
        col_widths = [
            available_width * 0.55,
            available_width * 0.225,
            available_width * 0.225,
        ]

        row_heights = [0.4 * inch] * len(combined_table_data)

        table = Table(
            combined_table_data,
            colWidths=col_widths,
            rowHeights=row_heights,
        )

        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

            ("SPAN", (0, 1), (-1, 1)),
            ("FONTNAME", (0, 1), (0, 1), "Helvetica-Bold"),
            ("BACKGROUND", (0, 1), (-1, 1), colors.whitesmoke),

            ("SPAN", (0, 8), (-1, 8)),
            ("FONTNAME", (0, 8), (0, 8), "Helvetica-Bold"),
            ("BACKGROUND", (0, 8), (-1, 8), colors.whitesmoke),

            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        story.append(table)
        story.append(Spacer(1, 0.15 * inch))


    sections = [
        ("4. EMPLOYEE RESPONSIBILITIES", [
            "4.1 You are expected to perform duties efficiently and in alignment with company goals.",
            "4.2 Adherence to company policies, confidentiality agreements, and professional ethics is mandatory."
        ]),
        ("5. WORKING HOURS & LEAVE POLICY", [
            f"5.1 Your official working hours will be Monday to Friday, {data['office_timings']}, with a lunch break.",
            "5.2 You will be entitled to paid leave as per company leave policy, details of which will be shared upon joining."
        ]),
        ("6. CONFIDENTIALITY & NON-COMPETE", [
            "6.1 You shall maintain confidentiality of all company data, employee records, financial details, and client information during and after employment.",
            f"6.2 You agree not to engage in any competing business or disclose proprietary information to third parties for a period of {data['probation_period']} months post-employment."
        ]),
        ("7. TERMINATION POLICY", [
            f"7.1 Resignation: If you wish to resign, you must serve a {data['notice_period']}-day notice period or pay in lieu of notice.",
            f"7.2 Termination: The company reserves the right to terminate employment with a {data['notice_period']}-day notice or immediate termination with compensation."
        ]),
        ("8. DOCUMENTS REQUIRED AT JOINING", [
            "You are required to submit the following documents:<br/>"
            "- Proof of Age & Identity (Aadhar/PAN/Passport)<br/>"
            "- Educational Certificates & Marksheets<br/>"
            "- Experience Letter (if applicable)<br/>"
            "- Last 3 Months' Salary Slips (if applicable)<br/>"
            "- 2 Passport-Sized Photographs<br/>"
            "- Bank Account Details for Salary Processing"
        ]),
        ("9. TERMS AND CONDITIONS", [
            f"9.1 Company Policies: You will adhere to all rules, regulations, and policies outlined by {data['office_name']}, including HR policies, compliance guidelines, and employee conduct requirements.",
            "9.2 Background Verification: Your employment is subject to successful completion of background verification, including educational and professional checks.",
            f"9.3 Intellectual Property: Any work created during your employment shall be the sole intellectual property of {data['office_name']}.",
            "9.4 Disciplinary Actions: Any violation of company policies, misconduct, or breach of confidentiality may lead to disciplinary actions, including termination.",
            "9.5 Retirement Age: The retirement age is 58 years as per company policy. Extensions may be granted at the discretion of the management."
        ]),
        (
            "10. ACCEPTANCE OF OFFER",
            [
                f"If you accept, kindly sign and return a copy of this letter by {data['acceptance_deadline']} as confirmation of your acceptance. Failure to respond by this date will result in withdrawal of this offer.",
                f"We are excited to have you on board and look forward to a successful journey together at {data['office_name']}.",
                "This Agreement is being issued in duplicate. Please return one copy duly signed immediately, as confirmation of your acceptance of the above terms and conditions.",
                f"<br/>Yours Faithfully,<br/>",
                f"<br/>________________________<br/><br/><b>{data['co_founder_and_director']}</b><br/>Co-Founder / Director – {data['office_name']}",
                "<br/><br/><b>Candidate Acknowledgment:</b><br/>"
                "I have read, understood, and accept the terms and conditions outlined in this offer letter."
                "<br/><br/>Signature: ________________________"
                f"<br/>Name: {data['name']}"
                "<br/>Date: ______________"
                f"<br/>Joining Date: {data['joining_date']}"
            ]
        )
    ]

    for title, lines in sections:
        story.append(Paragraph(title, section_style))
        for line in lines:
            story.append(Paragraph(line, list_style))
        story.append(Spacer(1, 0.1 * inch))

    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)

    buffer.seek(0)

    s3_client = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )
    file_bytes = s3_client.get_object(Bucket=AWS_S3_BUCKET, Key=template_path)['Body'].read()
    template_reader = PdfReader(io.BytesIO(file_bytes))
    overlay_reader = PdfReader(buffer)

    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    writer = PdfWriter()
    try:
        for i in range(len(template_reader.pages)):
            base_page = template_reader.pages[i]
            if i < len(overlay_reader.pages):
                base_page.merge_page(overlay_reader.pages[i])
            writer.add_page(base_page)
        buffer_out = BytesIO()
        writer.write(buffer_out)
        buffer_out.seek(0)
        return buffer_out.getvalue()
    except Exception as e:
        logger.error(f"Failed to save PDF to {template_path}: {str(e)}", exc_info=True)
        raise

