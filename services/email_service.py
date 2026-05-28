import base64
import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
ALLOW_CONSOLE_EMAIL_FALLBACK = os.getenv("ALLOW_CONSOLE_EMAIL_FALLBACK", "true").lower() == "true"


class EmailDeliveryError(Exception):
    pass


def build_verification_url(token: str) -> str:
    return f"{FRONTEND_URL}/verify?token={token}"


def _send_email(payload: dict[str, Any]) -> None:
    if not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_FROM_EMAIL:
        logger.warning("SMTP configuration missing. Skipping email: %s", payload.get("subject"))
        return

    message = EmailMessage()
    message["From"] = payload.get("from") or SMTP_FROM_EMAIL
    message["To"] = ", ".join(payload.get("to", []))
    message["Subject"] = payload.get("subject", "")
    message.set_content("This email requires an HTML-capable client.")
    message.add_alternative(payload.get("html", ""), subtype="html")

    for attachment in payload.get("attachments", []):
        content = attachment.get("content")
        filename = attachment.get("filename", "attachment.bin")
        maintype = attachment.get("maintype", "application")
        subtype = attachment.get("subtype", "octet-stream")
        if isinstance(content, str):
            decoded = base64.b64decode(content)
        else:
            decoded = content
        message.add_attachment(decoded, maintype=maintype, subtype=subtype, filename=filename)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:
        logger.exception("SMTP email failed for subject %s", payload.get("subject"))
        raise EmailDeliveryError(str(exc)) from exc


def _app_button(label: str, href: str) -> str:
    return (
        f'<a href="{href}" '
        'style="display:inline-block;padding:12px 18px;border-radius:12px;'
        'background:#6366F1;color:#fff;text-decoration:none;font-weight:600;">'
        f"{label}</a>"
    )


def send_verification_email(email: str, name: str, token: str) -> None:
    verify_url = build_verification_url(token)
    html = f"""
    <div style="font-family:Arial,sans-serif;background:#f5f7ff;padding:32px;">
      <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:20px;padding:32px;">
        <div style="font-size:24px;font-weight:700;color:#4338ca;margin-bottom:12px;">PingMe</div>
        <p style="font-size:16px;color:#111827;">Hi {name},</p>
        <p style="font-size:15px;color:#374151;line-height:1.6;">
          Welcome to PingMe. Verify your email to start turning follow-ups into smart reminders.
        </p>
        { _app_button("Verify Email", verify_url) }
        <p style="margin-top:24px;font-size:13px;color:#6b7280;">This link expires in 24 hours.</p>
      </div>
    </div>
    """
    _send_email(
        {
            "from": SMTP_FROM_EMAIL,
            "to": [email],
            "subject": "Verify your PingMe account",
            "html": html,
        }
    )


def log_verification_link(email: str, token: str) -> None:
    verify_url = build_verification_url(token)
    logger.warning("Verification email fallback for %s", email)
    logger.warning("Open this link manually: %s", verify_url)


def send_password_reset_email(email: str, name: str, token: str) -> None:
    reset_url = f"{FRONTEND_URL}/login?resetToken={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;background:#f5f7ff;padding:32px;">
      <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:20px;padding:32px;">
        <div style="font-size:24px;font-weight:700;color:#4338ca;margin-bottom:12px;">PingMe</div>
        <p style="font-size:16px;color:#111827;">Hi {name},</p>
        <p style="font-size:15px;color:#374151;line-height:1.6;">
          We received a request to reset your password. Use the button below if that was you.
        </p>
        { _app_button("Open PingMe", reset_url) }
        <p style="margin-top:24px;font-size:13px;color:#6b7280;">This link expires in 1 hour.</p>
      </div>
    </div>
    """
    _send_email(
        {
            "from": SMTP_FROM_EMAIL,
            "to": [email],
            "subject": "Reset your PingMe password",
            "html": html,
        }
    )


def send_reminder_email(user: dict[str, Any], reminder: dict[str, Any]) -> None:
    remind_at: datetime = reminder["remind_at"]
    html = f"""
    <div style="font-family:Arial,sans-serif;background:#eef2ff;padding:32px;">
      <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:22px;overflow:hidden;">
        <div style="background:#4f46e5;color:#fff;padding:22px 28px;font-size:24px;font-weight:700;">
          PingMe
        </div>
        <div style="padding:28px;">
          <p style="font-size:16px;color:#111827;">Hi {user['name']},</p>
          <p style="font-size:15px;color:#475569;line-height:1.7;">
            You have a PingMe reminder right now. Here is your pinned reminder so you can act on it immediately.
          </p>
          <div style="border:1px solid #c7d2fe;border-radius:16px;padding:18px;background:#f8faff;margin:20px 0;">
            <div style="font-size:18px;font-weight:700;color:#111827;">{reminder['title']}</div>
            <div style="margin-top:8px;color:#4f46e5;font-weight:600;">
              {remind_at.strftime('%d %b %Y, %I:%M %p')}
            </div>
          </div>
          { _app_button("Open PingMe", FRONTEND_URL) }
        </div>
      </div>
    </div>
    """
    _send_email(
        {
            "from": SMTP_FROM_EMAIL,
            "to": [user["email"]],
            "subject": f"You have a PingMe reminder: {reminder['title']}",
            "html": html,
        }
    )


def _escape_ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def build_calendar_invite(reminder: dict[str, Any], user: dict[str, Any]) -> str:
    remind_at: datetime = reminder["remind_at"].astimezone(IST)
    end_at = remind_at.replace(second=0, microsecond=0) + timedelta(hours=1)
    uid = f"pingme-{reminder.get('_id', reminder.get('id', 'event'))}@pingme.local"
    title = _escape_ics_text(reminder["title"])
    description = _escape_ics_text(reminder["raw_input"])
    organizer_email = SMTP_FROM_EMAIL.split("<")[-1].replace(">", "").strip()
    attendee_email = user["email"]
    attendee_name = _escape_ics_text(user.get("name") or attendee_email)
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "PRODID:-//PingMe//Reminder Invite//EN",
            "VERSION:2.0",
            "CALSCALE:GREGORIAN",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{remind_at.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{end_at.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{title}",
            f"DESCRIPTION:{description}",
            f"ATTENDEE;CN={attendee_name};RSVP=TRUE:mailto:{attendee_email}",
            f"ORGANIZER;CN=PingMe:mailto:{organizer_email}",
            "STATUS:CONFIRMED",
            "SEQUENCE:0",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


def send_calendar_invite_email(user: dict[str, Any], reminder: dict[str, Any]) -> None:
    remind_at: datetime = reminder["remind_at"].astimezone(IST)
    invite = build_calendar_invite(reminder, user)
    attachment = base64.b64encode(invite.encode("utf-8")).decode("utf-8")
    html = f"""
    <div style="font-family:Arial,sans-serif;background:#eef2ff;padding:32px;">
      <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:22px;overflow:hidden;">
        <div style="background:#4f46e5;color:#fff;padding:22px 28px;font-size:24px;font-weight:700;">
          PingMe Calendar Invite
        </div>
        <div style="padding:28px;">
          <p style="font-size:16px;color:#111827;">Hi {user['name']},</p>
          <p style="font-size:15px;color:#475569;line-height:1.7;">
            We attached a calendar invite for your reminder so you can add it to Google Calendar from your inbox.
          </p>
          <div style="border:1px solid #c7d2fe;border-radius:16px;padding:18px;background:#f8faff;margin:20px 0;">
            <div style="font-size:18px;font-weight:700;color:#111827;">{reminder['title']}</div>
            <div style="margin-top:8px;color:#4f46e5;font-weight:600;">
              {remind_at.strftime('%d %b %Y, %I:%M %p IST')}
            </div>
          </div>
          <p style="font-size:13px;color:#64748b;">
            Open the attached .ics file to add this reminder to your calendar.
          </p>
        </div>
      </div>
    </div>
    """
    _send_email(
        {
            "from": SMTP_FROM_EMAIL,
            "to": [user["email"]],
            "subject": f"Add to calendar: {reminder['title']}",
            "html": html,
            "attachments": [
                {
                    "content": attachment,
                    "filename": "pingme-reminder.ics",
                    "maintype": "text",
                    "subtype": "calendar",
                }
            ],
        }
    )


def send_client_followup_email(reminder: dict[str, Any], sender_name: Optional[str]) -> None:
    if not reminder.get("client_email"):
        return
    recipient_name = reminder.get("client_name") or "there"
    sender = sender_name or "PingMe user"
    message = reminder.get("client_message") or f"Following up regarding {reminder['title']}."
    subject_context = reminder.get("client_topic") or reminder.get("title") or "our conversation"
    html = f"""
    <div style="font-family:Arial,sans-serif;background:#f8fafc;padding:32px;">
      <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:20px;padding:32px;border:1px solid #e2e8f0;">
        <div style="font-size:22px;font-weight:700;color:#0f172a;margin-bottom:18px;">PingMe Follow-up</div>
        <p style="font-size:16px;color:#111827;">Hi {recipient_name},</p>
        <p style="font-size:15px;color:#334155;line-height:1.8;white-space:pre-line;">{message}</p>
        <p style="margin-top:24px;font-size:15px;color:#0f172a;">Best regards,<br />{sender}</p>
        <p style="margin-top:28px;font-size:12px;color:#94a3b8;">Sent with PingMe</p>
      </div>
    </div>
    """
    _send_email(
        {
            "from": SMTP_FROM_EMAIL,
            "to": [reminder["client_email"]],
            "subject": f"Following up on {subject_context}",
            "html": html,
        }
    )
