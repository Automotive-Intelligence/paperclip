"""
tools/outbound_email.py - Unified outbound email sender.

Delivery is via Resend HTTP API (https://resend.com) so Railway's outbound
SMTP block does not apply.  All sends go over HTTPS to api.resend.com.

Required Railway vars:
  RESEND_API_KEY                      - shared fallback key
  RESEND_API_KEY_<BUSINESSKEY>        - per-business override (optional)
  MAIL_FROM                           - shared fallback sender address
  MAIL_FROM_<BUSINESSKEY>             - per-business sender (e.g. MAIL_FROM_CALLINGDIGITAL)

EMAIL_DELIVERY_MODE must be set to "unified" to activate this path.
"""

import logging
import os
import re

import requests


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


def _resend_api_key(business_key: str = "") -> str:
    return _resolve_mail_setting("RESEND_API_KEY", business_key)


def unified_email_ready(business_key: str = "") -> bool:
    return bool(_resend_api_key(business_key) and _mail_from_for_business(business_key))


def send_unified_email(to_email: str, subject: str, body: str, business_key: str = "") -> bool:
    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to_email or not subject or not body or not unified_email_ready(business_key):
        return False

    api_key = _resend_api_key(business_key)
    from_email = _mail_from_for_business(business_key)

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_email,
                "to": [to_email],
                "subject": subject,
                "text": body,
            },
            timeout=20,
        )
        if resp.status_code in (200, 201):
            return True
        logging.error(
            "[outbound_email] Resend API error for %s (business=%s from=%s): HTTP %s %s",
            to_email, business_key, from_email, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:
        logging.error(
            "[outbound_email] Resend request failed for %s (business=%s): %s",
            to_email, business_key, exc,
        )
        return False
