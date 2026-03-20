"""
tools/outbound_email.py - Unified outbound email sender for Option B.

Supports a feature-flagged delivery mode:
  - native  (default): provider-specific send paths (GHL/HubSpot/Attio)
  - unified: single SMTP sender for all providers
"""

import os
import smtplib
from email.message import EmailMessage


def email_delivery_mode() -> str:
    mode = (os.getenv("EMAIL_DELIVERY_MODE") or "native").strip().lower()
    return mode if mode in {"native", "unified"} else "native"


def unified_email_ready() -> bool:
    required = (
        os.getenv("MAIL_HOST", "").strip(),
        os.getenv("MAIL_PORT", "").strip(),
        os.getenv("MAIL_USERNAME", "").strip(),
        os.getenv("MAIL_PASSWORD", "").strip(),
        os.getenv("MAIL_FROM", "").strip(),
    )
    return all(required)


def send_unified_email(to_email: str, subject: str, body: str) -> bool:
    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to_email or not subject or not body or not unified_email_ready():
        return False

    host = os.getenv("MAIL_HOST", "").strip()
    port = int((os.getenv("MAIL_PORT", "587") or "587").strip())
    username = os.getenv("MAIL_USERNAME", "").strip()
    password = os.getenv("MAIL_PASSWORD", "").strip()
    from_email = os.getenv("MAIL_FROM", "").strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
        return True
    except Exception:
        return False
