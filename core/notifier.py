"""Notifications for Project Paperclip — logs only."""

import os
import time
import logging
import hashlib
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_hot_lead_loggers: dict = {}

# In-process de-dupe cache for hot-reply SMS. A single Instantly reply can flow
# through BOTH the Instantly webhook handler and the intent-inbound pipeline
# (via the shim). We key on (brand, person, channel) and suppress a repeat
# within _HOT_REPLY_DEDUPE_TTL seconds so Michael gets one text, not two.
# This is best-effort only: it lives in process memory, so it does NOT dedupe
# across worker processes / restarts. That is acceptable for an alert (worst
# case = a rare duplicate text), and is noted in the PR.
_hot_reply_sent_at: dict = {}
_HOT_REPLY_DEDUPE_TTL = 300  # seconds


def _get_hot_lead_logger(river: str) -> logging.Logger:
    if river in _hot_lead_loggers:
        return _hot_lead_loggers[river]
    logger = logging.getLogger(f"paperclip.hot_leads.{river}")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(LOG_DIR, f"{river}_hot_leads.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(message)s"))
    logger.addHandler(sh)
    _hot_lead_loggers[river] = logger
    return logger


def notify_hot_lead(river: str, contact_name: str, business: str, phone: str, action: str):
    logger = _get_hot_lead_logger(river)
    logger.info(f"HOT LEAD | {contact_name} | {business} | {phone} | {action}")


def notify_error(river: str, error_message: str):
    logger = _get_hot_lead_logger(river)
    logger.error(f"ERROR | {error_message}")


def notify_daily_summary(stats: dict):
    logger = _get_hot_lead_logger("summary")
    lines = [f"DAILY SUMMARY — {len(stats)} rivers"]
    for river, data in stats.items():
        lines.append(f"  {river}: {data.get('enrolled', 0)} enrolled, {data.get('hot_leads', 0)} hot leads")
    logger.info("\n".join(lines))


def _twilio_config():
    """Resolve Twilio send config, preferring a Standard API key over the
    account Auth Token.

    Auth precedence:
      1. If TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET are both set, Basic Auth
         uses (TWILIO_API_KEY_SID, TWILIO_API_KEY_SECRET).
      2. Otherwise, fall back to (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).

    In BOTH cases the request URL path uses the Account SID (AC..., from
    TWILIO_ACCOUNT_SID) — never the key SID. The API key only changes the Basic
    Auth credentials, not the /Accounts/{AccountSid}/ path.

    Returns (url, auth_tuple, from_number, to_number) when a complete + valid
    config is present, else None so callers can no-op cleanly. The guard
    requires: TWILIO_ACCOUNT_SID + TWILIO_FROM + MICHAEL_PHONE + either
    (API_KEY_SID and API_KEY_SECRET) or AUTH_TOKEN.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    from_number = os.environ.get("TWILIO_FROM")
    michael_phone = os.environ.get("MICHAEL_PHONE")
    api_key_sid = os.environ.get("TWILIO_API_KEY_SID")
    api_key_secret = os.environ.get("TWILIO_API_KEY_SECRET")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

    if not all([account_sid, from_number, michael_phone]):
        return None

    if api_key_sid and api_key_secret:
        auth = (api_key_sid, api_key_secret)
    elif auth_token:
        auth = (account_sid, auth_token)
    else:
        return None

    # URL path uses the ACCOUNT SID, regardless of which auth pair we send.
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    return url, auth, from_number, michael_phone


def notify_cost_alert(message: str):
    """Send cost budget alert — logs + Twilio when configured."""
    logger = _get_hot_lead_logger("cost_alerts")
    logger.warning(f"COST ALERT | {message}")

    # Twilio alert when credentials are available (API key preferred, else token)
    cfg = _twilio_config()
    if cfg:
        url, auth, from_number, michael_phone = cfg
        try:
            import requests
            requests.post(url, auth=auth, data={
                "From": from_number,
                "To": michael_phone,
                "Body": message,
            })
            logger.info("COST ALERT sent via Twilio to Michael")
        except Exception as e:
            logger.error(f"Twilio cost alert failed: {e}")


def notify_axiom_directive(target_agent: str, directive: str, triggered_by: str):
    """Log Axiom CEO directive — for traceability."""
    logger = _get_hot_lead_logger("axiom")
    logger.info(f"AXIOM DIRECTIVE | to={target_agent} | trigger={triggered_by} | {directive[:100]}")


def notify_hot_reply(brand: str, person_ref: str, channel: str, snippet: str = "") -> bool:
    """Text Michael when a genuine prospect REPLY lands via webhook.

    Call this ONLY on real positive/neutral reply events (reply_received /
    interested / inbound_reply). Do NOT call it on bounces or unsubscribes.

    Guarded like notify_cost_alert: reads TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_FROM, MICHAEL_PHONE and no-ops cleanly when ANY of them is absent, so
    it is safe to deploy before Twilio creds / A2P are live. Returns True iff an
    SMS was actually POSTed to Twilio; False when it was skipped (no creds, or
    de-duped, or the POST failed).

    Args:
        brand:      short brand tag (e.g. "avi", "wd", "aipg").
        person_ref: who replied — email / phone / name, whatever we have.
        channel:    where it came in (e.g. "cold_email", "sms", "inbound_call").
        snippet:    short preview of the reply body (trimmed to keep SMS short).
    """
    logger = _get_hot_lead_logger("hot_reply")

    brand = (brand or "?").strip()
    person_ref = (person_ref or "?").strip()
    channel = (channel or "?").strip()
    snippet = (snippet or "").strip()

    # De-dupe: same brand+person+channel within the TTL sends once.
    key = hashlib.sha256(f"{brand}|{person_ref.lower()}|{channel}".encode("utf-8")).hexdigest()
    now = time.time()
    last = _hot_reply_sent_at.get(key)
    if last is not None and (now - last) < _HOT_REPLY_DEDUPE_TTL:
        logger.info(f"HOT REPLY de-duped (within {_HOT_REPLY_DEDUPE_TTL}s) | {brand} | {person_ref} | {channel}")
        return False

    logger.info(f"HOT REPLY | {brand} | {person_ref} | {channel} | {snippet[:120]}")

    # API key preferred, else account Auth Token. No-ops cleanly if neither a
    # complete key pair nor a token is present (plus Account SID/from/to).
    cfg = _twilio_config()
    if not cfg:
        logger.info("HOT REPLY not texted (Twilio creds/MICHAEL_PHONE not set) — logged only")
        return False
    url, auth, from_number, michael_phone = cfg

    # Keep the SMS short and useful. No em-dashes in outbound copy.
    snippet_out = snippet[:100] + ("..." if len(snippet) > 100 else "")
    body_parts = [f"New {brand} reply", f"from {person_ref}", f"via {channel}"]
    if snippet_out:
        body_parts.append(f'"{snippet_out}"')
    body_parts.append("Check the CRM.")
    body = ". ".join(body_parts)

    try:
        import requests
        resp = requests.post(url, auth=auth, data={
            "From": from_number,
            "To": michael_phone,
            "Body": body,
        }, timeout=15)
        if resp.status_code >= 400:
            logger.error(f"HOT REPLY Twilio POST failed: {resp.status_code} {resp.text[:200]}")
            return False
        _hot_reply_sent_at[key] = now
        logger.info("HOT REPLY texted to Michael via Twilio")
        return True
    except Exception as e:
        logger.error(f"HOT REPLY Twilio send failed: {e}")
        return False
