"""
tools/outbound_email.py - Unified outbound email sender for Option B.

Supports a feature-flagged delivery mode:
  - native  (default): provider-specific send paths (GHL/HubSpot/Attio)
  - unified: single SMTP sender for all providers
"""

import os
import smtplib
import re
from email.message import EmailMessage


def email_delivery_mode() -> str:
    mode = (os.getenv("EMAIL_DELIVERY_MODE") or "native").strip().lower()
    return mode if mode in {"native", "unified"} else "native"


def _mail_from_for_business(business_key: str = "") -> str:
    key = (business_key or "").strip().lower()
    if key:
        normalized = re.sub(r"[^a-z0-9]", "", key).upper()
        business_from = os.getenv(f"MAIL_FROM_{normalized}", "").strip()
        if business_from:
            return business_from
    return os.getenv("MAIL_FROM", "").strip()


def unified_email_ready(business_key: str = "") -> bool:
    required = (
        os.getenv("MAIL_HOST", "").strip(),
        os.getenv("MAIL_PORT", "").strip(),
        os.getenv("MAIL_USERNAME", "").strip(),
        os.getenv("MAIL_PASSWORD", "").strip(),
        _mail_from_for_business(business_key),
    )
    return all(required)


def send_unified_email(to_email: str, subject: str, body: str, business_key: str = "") -> bool:
    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to_email or not subject or not body or not unified_email_ready(business_key):
        return False

    host = os.getenv("MAIL_HOST", "").strip()
    port = int((os.getenv("MAIL_PORT", "587") or "587").strip())
    username = os.getenv("MAIL_USERNAME", "").strip()
    password = os.getenv("MAIL_PASSWORD", "").strip()
    from_email = _mail_from_for_business(business_key)

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
