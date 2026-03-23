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


def _business_env_suffix(business_key: str = "") -> str:
    key = (business_key or "").strip().lower()
    if not key:
        return ""
    return re.sub(r"[^a-z0-9]", "", key).upper()


def _resolve_mail_setting(base_name: str, business_key: str = "", default: str = "") -> str:
    suffix = _business_env_suffix(business_key)
    if suffix:
        value = os.getenv(f"{base_name}_{suffix}", "").strip()
        if value:
            return value
    return os.getenv(base_name, default).strip()


def email_delivery_mode() -> str:
    mode = (os.getenv("EMAIL_DELIVERY_MODE") or "native").strip().lower()
    return mode if mode in {"native", "unified"} else "native"


def _mail_from_for_business(business_key: str = "") -> str:
    return _resolve_mail_setting("MAIL_FROM", business_key)


def unified_email_ready(business_key: str = "") -> bool:
    required = (
        _resolve_mail_setting("MAIL_HOST", business_key),
        _resolve_mail_setting("MAIL_PORT", business_key),
        _resolve_mail_setting("MAIL_USERNAME", business_key),
        _resolve_mail_setting("MAIL_PASSWORD", business_key),
        _mail_from_for_business(business_key),
    )
    return all(required)


def send_unified_email(to_email: str, subject: str, body: str, business_key: str = "") -> bool:
    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to_email or not subject or not body or not unified_email_ready(business_key):
        return False

    host = _resolve_mail_setting("MAIL_HOST", business_key)
    port = int((_resolve_mail_setting("MAIL_PORT", business_key, "587") or "587").strip())
    username = _resolve_mail_setting("MAIL_USERNAME", business_key)
    password = _resolve_mail_setting("MAIL_PASSWORD", business_key)
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
