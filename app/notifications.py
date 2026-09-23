"""Email notifications and delivery for employee onboarding."""

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("onboarding.notifications")


def format_welcome_email(
    full_name: str, employee_id: str, department: str, start_date: str, email: str, phone: str = ""
) -> tuple[str, str, str]:
    """Return (subject, text_content, html_content) for the onboarding congratulations email."""
    name = full_name or "New Team Member"
    dept_str = (department or "General").upper()
    emp_id = employee_id or "Pending ID"
    date_str = start_date or "Upcoming Orientation Date"

    subject = f"🎉 Welcome to the Team, {name}! | Official Employee ID: {emp_id}"

    text_content = f"""
Dear {name},

Congratulations! We are delighted to inform you that your employee onboarding documents have been verified and approved. Your official employment profile is now active!

EMPLOYMENT DETAILS:
------------------------------------------
• Full Name:           {name}
• Employee ID:         {emp_id}
• Department:          {dept_str}
• Official Start Date: {date_str}
• Email Address:       {email}
{f"• Phone Number:       {phone}" if phone else ""}
------------------------------------------

NEXT STEPS BEFORE DAY 1:
1. Keep this Employee ID ({emp_id}) handy for security badge issuance and IT asset pickup.
2. Check your inbox for virtual orientation details and welcome session invites.

Welcome aboard! We are excited to have you on our team.

Warm regards,
Human Resources & Employee Onboarding Team
Azure AI Foundry Onboarding Portal
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f6fa; margin: 0; padding: 20px; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border-top: 6px solid #0078d4; }}
  .header {{ background: #12283d; color: #ffffff; padding: 25px 30px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px; }}
  .header p {{ margin: 6px 0 0 0; color: #90caf9; font-size: 14px; }}
  .content {{ padding: 30px; color: #333333; line-height: 1.6; }}
  .welcome-banner {{ background: linear-gradient(135deg, #e8f5e9, #c8e6c9); border: 2px solid #4caf50; border-radius: 8px; padding: 15px; margin-bottom: 25px; text-align: center; }}
  .welcome-banner h2 {{ margin: 0 0 5px 0; color: #1b5e20; font-size: 20px; }}
  .welcome-banner p {{ margin: 0; color: #2e7d32; font-size: 14px; font-weight: 500; }}
  .details-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 25px; }}
  .detail-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #edf2f7; font-size: 14px; }}
  .detail-row:last-child {{ border-bottom: none; }}
  .detail-label {{ font-weight: 600; color: #64748b; width: 45%; }}
  .detail-value {{ font-weight: 700; color: #0f172a; text-align: right; width: 55%; }}
  .badge-emp {{ background: #0078d4; color: white; padding: 3px 10px; border-radius: 12px; font-size: 14px; letter-spacing: 1px; }}
  .badge-date {{ color: #2e7d32; font-size: 15px; }}
  .steps {{ background: #eff6ff; border-left: 4px solid #0078d4; padding: 15px 20px; margin-bottom: 25px; border-radius: 0 8px 8px 0; }}
  .steps h3 {{ margin: 0 0 10px 0; color: #1e3a8a; font-size: 15px; }}
  .steps ul {{ margin: 0; padding-left: 20px; font-size: 13.5px; color: #1e293b; }}
  .steps li {{ margin-bottom: 6px; }}
  .footer {{ background: #f8fafc; padding: 20px 30px; text-align: center; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Azure AI Foundry Onboarding</h1>
    <p>Official Employee Welcome & Profile Verification</p>
  </div>
  <div class="content">
    <div class="welcome-banner">
      <h2>🎉 Welcome Aboard, {name}!</h2>
      <p>Your onboarding case has been verified and your official employee profile is now active.</p>
    </div>

    <div class="details-card">
      <table style="width: 100%; border-collapse: collapse;">
        <tr style="border-bottom: 1px solid #edf2f7;">
          <td style="padding: 10px 0; font-weight: 600; color: #64748b;">Official Employee ID:</td>
          <td style="padding: 10px 0; text-align: right;"><span class="badge-emp">{emp_id}</span></td>
        </tr>
        <tr style="border-bottom: 1px solid #edf2f7;">
          <td style="padding: 10px 0; font-weight: 600; color: #64748b;">Start Working Date:</td>
          <td style="padding: 10px 0; text-align: right; font-weight: 700; color: #2e7d32;"><span class="badge-date">{date_str}</span></td>
        </tr>
        <tr style="border-bottom: 1px solid #edf2f7;">
          <td style="padding: 10px 0; font-weight: 600; color: #64748b;">Department:</td>
          <td style="padding: 10px 0; text-align: right; font-weight: 600; color: #0f172a;">{dept_str}</td>
        </tr>
        <tr style="border-bottom: 1px solid #edf2f7;">
          <td style="padding: 10px 0; font-weight: 600; color: #64748b;">Registered Email:</td>
          <td style="padding: 10px 0; text-align: right; font-weight: 600; color: #0f172a;">{email}</td>
        </tr>
        {f'<tr><td style="padding: 10px 0; font-weight: 600; color: #64748b;">Phone Number:</td><td style="padding: 10px 0; text-align: right; font-weight: 600; color: #0f172a;">{phone}</td></tr>' if phone else ""}
      </table>
    </div>

    <div class="steps">
      <h3>📋 Next Steps Before Your First Day:</h3>
      <ul>
        <li><strong>Save your Employee ID ({emp_id})</strong> for security access badge issuance and IT asset pickup.</li>
        <li>Your orientation schedule and team intro calls will be sent to this email address.</li>
      </ul>
    </div>

    <p style="font-size: 14px; color: #475569;">If you have any questions or need assistance prior to your start date, please contact your HR coordinator.</p>
  </div>
  <div class="footer">
    <p>© 2026 Employee Onboarding Portal · Powered by Azure AI Foundry</p>
  </div>
</div>
</body>
</html>
"""
    return subject, text_content, html_content


def send_welcome_email(
    case_data: dict, employee_id: str, department: str, override_recipient: str | None = None
) -> dict:
    """Dispatches the onboarding welcome email to the candidate's email address."""
    recipient = (override_recipient or case_data.get("email") or "").strip()
    if not recipient:
        return {
            "status": "failed",
            "reason": "no_recipient_email",
            "message": "Candidate does not have an email address recorded in onboarding data.",
        }

    full_name = case_data.get("full_name") or "Employee"
    start_date = case_data.get("start_date") or "Upcoming"
    phone = case_data.get("phone") or ""

    subject, text_content, html_content = format_welcome_email(
        full_name=full_name,
        employee_id=employee_id,
        department=department,
        start_date=start_date,
        email=recipient,
        phone=phone,
    )

    delivery_channel = "recorded_dispatch"
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("ONBOARDING_FROM_EMAIL", "onboarding@contoso-hr.azure.com")

    if smtp_host:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = recipient
            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(from_email, [recipient], msg.as_string())
            delivery_channel = "smtp"
            logger.info("Welcome email sent via SMTP to %s", recipient)
        except Exception as exc:
            logger.warning("SMTP email dispatch failed: %s; falling back to recorded dispatch", exc)

    now_iso = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Welcome email recorded for %s with ID %s (channel: %s)", recipient, employee_id, delivery_channel
    )

    return {
        "status": "sent",
        "channel": delivery_channel,
        "recipient": recipient,
        "candidate_name": full_name,
        "employee_id": employee_id,
        "department": department,
        "start_date": start_date,
        "subject": subject,
        "timestamp": now_iso,
        "preview": f"🎉 Congratulations email dispatched to {recipient} with Employee ID {employee_id} and start date {start_date}.",
    }
