"""services/bookd_handoff.py -- encrypted secret staging for the Book'd agent port.

The founding use case: Ryan's agent hands us a Stripe key. The value must never sit in
an email, a log line, or a conversation row. Flow:

    partner agent -> stage(key_name, value) -> Fernet-encrypted row in Postgres
    -> Michael is emailed the key NAME (never the value)
    -> Michael (or B&T on his word) runs /admin/bookd-handoff-reveal once, installs it
       into Doppler by hand, then /admin/bookd-handoff-applied.

Reveal is ONE-SHOT: a revealed row cannot be revealed again (re-stage if lost). Install
stays a human action on purpose -- paperclip holds no Doppler write token, and money
keys should have a human approval step. Encryption reuses the postal token pattern
(Fernet key derived from APP_SECRET). Every external effect is a module seam.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List

import requests

from services.database import execute_query, fetch_all
from services.postal_oauth import _fernet  # deliberate reuse: one APP_SECRET-derived key

logger = logging.getLogger(__name__)

_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,64}$")
_MAX_VALUE_CHARS = 4096

_CREATE = """
CREATE TABLE IF NOT EXISTS bookd_handoff_staging (
    id           BIGSERIAL PRIMARY KEY,
    key_name     TEXT NOT NULL,
    value_enc    TEXT NOT NULL,
    submitted_by TEXT,
    status       TEXT NOT NULL DEFAULT 'staged',   -- staged | revealed | applied
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _alert(subject: str, text: str) -> bool:
    """Plain-text escalation to Michael on the verified Resend rail. Never a value."""
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    to_addr = (os.getenv("LEAD_ALERT_TO") or "michael@automotiveintelligence.io").strip()
    frm = os.getenv("LEAD_ALERT_FROM", "AVO <cmo@mail.automotiveintelligence.io>")
    if not key:
        logger.error("[bookd-handoff] RESEND_API_KEY missing; cannot alert: %s", subject)
        return False
    try:
        r = requests.post(
            "https://api.resend.com/emails", timeout=15,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": frm, "to": [to_addr], "subject": subject, "text": text})
        return r.ok
    except requests.RequestException:
        logger.exception("[bookd-handoff] alert failed")
        return False


def stage(key_name: str, value: str, submitted_by: str = "partner") -> Dict[str, Any]:
    """Encrypt + stage one secret value. Emails Michael the key NAME only."""
    name = (key_name or "").strip().upper()
    if not _KEY_NAME_RE.match(name):
        return {"ok": False, "error": "invalid key_name (expected e.g. STRIPE_WEBHOOK_SECRET)"}
    val = (value or "").strip()
    if not val or len(val) > _MAX_VALUE_CHARS:
        return {"ok": False, "error": "value empty or too large"}

    enc = _fernet().encrypt(val.encode()).decode()
    execute_query(_CREATE)
    rows = fetch_all(
        "INSERT INTO bookd_handoff_staging (key_name, value_enc, submitted_by) "
        "VALUES (%s,%s,%s) RETURNING id", (name, enc, (submitted_by or "partner")[:80]))
    hid = int(rows[0][0])
    _alert(
        f"[Book'd port] secret staged: {name} (#{hid})",
        "Ryan's agent staged a credential through the Book'd agent port.\n\n"
        f"  key:    {name}\n  id:     {hid}\n  by:     {submitted_by}\n\n"
        "The value is Fernet-encrypted at rest and was never emailed or logged.\n"
        "To install: GET /admin/bookd-handoffs, POST /admin/bookd-handoff-reveal "
        "{\"id\": %d} (ONE-SHOT), place it in Doppler bookd/prd, then POST "
        "/admin/bookd-handoff-applied." % hid)
    logger.info("[bookd-handoff] staged %s as #%d (by %s)", name, hid, submitted_by)
    return {"ok": True, "id": hid, "key_name": name, "status": "staged"}


def list_pending() -> List[Dict[str, Any]]:
    """Staged-or-revealed rows awaiting install. Values never leave this module here."""
    execute_query(_CREATE)
    rows = fetch_all(
        "SELECT id, key_name, submitted_by, status, created_at FROM bookd_handoff_staging "
        "WHERE status IN ('staged','revealed') ORDER BY id")
    return [{"id": int(r[0]), "key_name": r[1], "submitted_by": r[2],
             "status": r[3], "created_at": str(r[4])} for r in rows]


def reveal(hid: int) -> Dict[str, Any]:
    """ONE-SHOT decrypt for Michael's install step. A revealed row never reveals again."""
    rows = fetch_all(
        "SELECT key_name, value_enc, status FROM bookd_handoff_staging WHERE id=%s", (hid,))
    if not rows:
        return {"ok": False, "error": f"no handoff #{hid}"}
    name, enc, status = rows[0]
    if status != "staged":
        return {"ok": False, "error": f"handoff #{hid} already {status} (one-shot reveal)"}
    value = _fernet().decrypt(enc.encode()).decode()
    execute_query("UPDATE bookd_handoff_staging SET status='revealed' WHERE id=%s", (hid,))
    logger.info("[bookd-handoff] revealed #%d (%s)", hid, name)
    return {"ok": True, "id": hid, "key_name": name, "value": value}


def mark_applied(hid: int) -> Dict[str, Any]:
    execute_query("UPDATE bookd_handoff_staging SET status='applied' WHERE id=%s", (hid,))
    return {"ok": True, "id": hid, "status": "applied"}
