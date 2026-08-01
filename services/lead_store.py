"""services/lead_store.py -- the lead SYSTEM OF RECORD (funnel standard items 5, 6).

The teardown finding (deliverable 155): every brand tells the user it succeeded but
never reliably tells a human a lead arrived, and no brand has a durable record of its
own. This is the fix. Every lead is:

  1. written to durable Postgres FIRST (item 5: the record is the system of record,
     BEFORE any notification), idempotently (item 6: replaying a payload = one lead),
  2. then pushed to GHL with retry (5xx retried, 4xx not), and
  3. then a human is ALWAYS alerted -- never gated on the CRM write succeeding
     (deliverable 156 Phase 2: gating notification on CRM failure is the exact defect).

A GHL write that fails after retries lands in `status='dead_letter'` and STILL alerts a
human. `recent_count()` feeds absence alerting (#17) and daily reconciliation (#7); the
synthetic canary (#8) submits with synthetic=True and verifies its row landed here.

The receipt is FAIL CLOSED: ok is True only when the lead is durably stored AND a human
was alerted (or it is a synthetic canary, which verifies the plumbing without paging).
Every external effect is a module seam so tests never touch Postgres, GHL, or email.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import requests

from services.database import execute_query, fetch_all
from services.lead_capture import _alert_email, _alert_issue

logger = logging.getLogger(__name__)

_FIELDS = ("name", "phone", "email", "trade", "message", "source", "business")
_GHL_MAX_ATTEMPTS = 3

_CREATE = """
CREATE TABLE IF NOT EXISTS leads (
    idempotency_key TEXT PRIMARY KEY,
    brand           TEXT NOT NULL,
    name TEXT, phone TEXT, email TEXT, trade TEXT, message TEXT, source TEXT,
    ghl_ok          BOOLEAN NOT NULL DEFAULT FALSE,
    alerted         BOOLEAN NOT NULL DEFAULT FALSE,
    alert_via       TEXT,
    is_synthetic    BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'new',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _ensure_table() -> None:
    execute_query(_CREATE)


def _idempotency_key(payload: Dict[str, Any]) -> str:
    """A client-supplied key wins; otherwise the same person in the same hour is one
    lead (dedups a double-click / a retried webhook without dropping a genuine repeat
    a day later)."""
    explicit = str(payload.get("idempotency_key") or "").strip()
    if explicit:
        return explicit[:200]
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    basis = "|".join([str(payload.get("brand") or ""),
                      str(payload.get("email") or "").lower().strip(),
                      str(payload.get("phone") or "").strip(),
                      str(payload.get("name") or "").lower().strip(), hour])
    return hashlib.sha256(basis.encode()).hexdigest()[:48]


def _ghl_write(lead: Dict[str, Any]) -> bool:
    """Write the contact to GHL. 5xx is retried, 4xx (bad payload / dup) is not."""
    key = (os.getenv("GHL_API_KEY") or "").strip()
    loc = (os.getenv("GHL_LOCATION_ID") or "").strip()
    if not key or not loc:
        logger.error("[lead_store] GHL creds missing; cannot write CRM")
        return False
    name = (lead.get("name") or "").strip()
    parts = name.split()
    first, last = (parts[0] if parts else ""), (" ".join(parts[1:]) if len(parts) > 1 else "")
    tags = ["website-lead"] + [t for t in (
        (f"src:{lead['source']}" if lead.get("source") else ""),
        (f"trade:{lead['trade']}" if lead.get("trade") else "")) if t]
    for attempt in range(_GHL_MAX_ATTEMPTS):
        try:
            r = requests.post(
                "https://services.leadconnectorhq.com/contacts/", timeout=15,
                headers={"Authorization": f"Bearer {key}", "Version": "2021-07-28",
                         "Content-Type": "application/json"},
                json={"locationId": loc, "firstName": first, "lastName": last,
                      "email": lead.get("email") or None, "phone": lead.get("phone") or None,
                      "companyName": lead.get("business") or None,
                      "source": lead.get("source") or "aipg-website", "tags": tags})
            if r.ok:
                return True
            if r.status_code < 500:
                logger.error("[lead_store] GHL %s (non-retryable): %s", r.status_code, r.text[:160])
                return False
            logger.warning("[lead_store] GHL %s attempt %d/%d, retrying",
                           r.status_code, attempt + 1, _GHL_MAX_ATTEMPTS)
        except requests.RequestException as e:
            logger.warning("[lead_store] GHL exception attempt %d: %s", attempt + 1, e)
    return False


def ingest_lead(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Durable-first, idempotent ingest with an always-fire alert. Fail-closed receipt."""
    brand = str(payload.get("brand") or "unknown")
    lead = {k: (str(payload.get(k) or "")[:400] or None) for k in _FIELDS}
    synthetic = bool(payload.get("synthetic"))
    key = _idempotency_key(payload)

    # 1. DURABLE STORE FIRST (item 5), idempotent (item 6). ON CONFLICT = replay dedup.
    try:
        _ensure_table()
        execute_query(
            "INSERT INTO leads (idempotency_key, brand, name, phone, email, trade, message, "
            "source, is_synthetic) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (idempotency_key) DO NOTHING",
            (key, brand, lead["name"], lead["phone"], lead["email"], lead["trade"],
             lead["message"], lead["source"], synthetic))
    except Exception as e:
        logger.exception("[lead_store] DURABLE STORE FAILED -- cannot confirm capture")
        return {"ok": False, "stored": False, "error": f"store failed: {e}", "key": key}

    # Replay? If this key already alerted on a prior call, dedup (do not re-GHL/re-alert).
    prior = fetch_all("SELECT alerted, alert_via FROM leads WHERE idempotency_key=%s", (key,))
    if prior and prior[0][0]:
        return {"ok": True, "stored": True, "deduped": True,
                "alerted": True, "via": prior[0][1], "key": key}

    # 2. GHL (retried). 3. ALWAYS alert a human (never gate on GHL), unless synthetic.
    ghl_ok = False if synthetic else _ghl_write(lead)
    if synthetic:
        alerted, via = True, "synthetic"      # canary verifies plumbing; no human paged
    else:
        alert_lead = {k: (lead.get(k) or "") for k in _FIELDS}
        emailed = _alert_email(brand, alert_lead)
        issued = False if emailed else _alert_issue(brand, alert_lead)
        alerted = bool(emailed or issued)
        via = "email" if emailed else ("issue" if issued else "receipt-only")

    status = "delivered" if ghl_ok else ("verified" if synthetic else "dead_letter")
    execute_query(
        "UPDATE leads SET ghl_ok=%s, alerted=%s, alert_via=%s, status=%s WHERE idempotency_key=%s",
        (ghl_ok, alerted, via, status, key))
    if status == "dead_letter":
        logger.error("[lead_store] DEAD-LETTER %s lead %s: GHL failed after retries "
                     "(human alerted=%s)", brand, key, alerted)

    # FAIL CLOSED: True only if stored AND a human was told (or synthetic canary).
    return {"ok": bool(alerted), "stored": True, "ghl_ok": ghl_ok,
            "alerted": alerted, "via": via, "status": status, "key": key}


def recent_count(brand: str, hours: int, *, synthetic: bool = False) -> int:
    """Real (or synthetic) leads for a brand in the trailing window. Feeds #17 absence
    alerting and #7 reconciliation."""
    rows = fetch_all(
        "SELECT COUNT(*) FROM leads WHERE brand=%s AND is_synthetic=%s "
        "AND created_at > NOW() - make_interval(hours => %s)",
        (brand, synthetic, hours))
    return int(rows[0][0]) if rows else 0
