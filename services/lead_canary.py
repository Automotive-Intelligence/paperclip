"""services/lead_canary.py -- funnel standard item #8: the synthetic canary.

A funnel that silently drops leads produces NO error (deliverable 155's core finding:
"every brand tells the user it succeeded, no brand reliably tells a human"). The only
thing that proves the delivery layer (#5 durable store, #6 idempotent+alert) is real is
to push a real lead through the real path on a schedule and check it actually landed.

This runs a SYNTHETIC lead (is_synthetic=True, so lead_store skips GHL + the human page)
and verifies the durable row landed and the alert path was exercised. It scores two
things SEPARATELY, per the standard:

  responded  -- the path acknowledged AND the lead is durably in the system of record.
                This is what B&T's delivery layer owns and what the 48h-green gate reads.
  answered   -- did a HUMAN actually answer the question asked (vs an autoresponder).
                That is the speed/human layer (CRO #9-#14); a synthetic can't verify a
                human reply, so it is recorded as 'manual' -- a human mystery-shop cadence
                fills it, and the two scores never collapse into one.

TARGET: if AIPG_CANARY_FORM_URL is set, the canary POSTs to the REAL public form
(theaiphoneguy.ai/api/lead) with the shared canary secret, so the WHOLE chain
(form -> route -> /lead/ingest -> store + alert) is exercised -- this is the #8 the gate
requires. Until that route is deployed, it falls back to calling ingest_lead directly,
which proves the delivery CORE live now. The target is recorded on every run so the
48h-green proof is honest about which path was tested.

FAILS LOUDLY: a red canary emails Michael on the verified Resend rail. Every external
effect is a module seam so tests never touch Postgres, HTTP, or email.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional

import requests

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_BRAND = "aipg"
_RESEND = "https://api.resend.com/emails"

_CREATE = """
CREATE TABLE IF NOT EXISTS canary_runs (
    id        BIGSERIAL PRIMARY KEY,
    brand     TEXT NOT NULL,
    target    TEXT,                 -- 'form' (real public form) | 'ingest' (delivery core)
    responded BOOLEAN NOT NULL DEFAULT FALSE,
    answered  TEXT,                 -- 'manual' (human mystery-shop owns this) | 'pass' | 'fail'
    lead_key  TEXT,
    detail    TEXT,
    ran_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _synthetic_payload() -> Dict[str, Any]:
    tok = uuid.uuid4().hex[:12]
    return {
        "brand": _BRAND,
        "name": f"AVO Canary {tok}",
        "email": f"canary+{tok}@automotiveintelligence.io",
        "trade": "canary",
        "source": "funnel-canary",
        "message": "Synthetic funnel canary. Do not action. Proves the lead path is alive.",
        "synthetic": True,
        "idempotency_key": f"canary-{tok}",
    }


def _submit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Push the synthetic lead through the REAL public form if configured, else the
    delivery core directly. Returns {target, ack} where ack is the path's own receipt."""
    form_url = (os.getenv("AIPG_CANARY_FORM_URL") or "").strip()
    secret = (os.getenv("LEAD_CANARY_SECRET") or "").strip()
    if form_url and secret:
        try:
            r = requests.post(form_url, timeout=20,
                              headers={"Content-Type": "application/json",
                                       "x-canary-secret": secret},
                              json=payload)
            ack = {"http": r.status_code, "ok": r.ok,
                   "body": (r.json() if r.content and r.ok else r.text[:200])}
            return {"target": "form", "ack": ack}
        except requests.RequestException as e:
            return {"target": "form", "ack": {"ok": False, "error": str(e)}}
    # Fallback: exercise the delivery core directly (still proves #5/#6 live).
    from services.lead_store import ingest_lead
    return {"target": "ingest", "ack": ingest_lead(payload)}


def _verify_durable(key: str) -> Dict[str, Any]:
    """Read the system of record back: did the synthetic lead actually land, and was the
    alert path exercised? This is the load-bearing check -- an ack is not enough."""
    rows = fetch_all(
        "SELECT is_synthetic, alerted, status FROM leads WHERE idempotency_key=%s", (key,))
    if not rows:
        return {"stored": False, "alerted": False, "status": None}
    is_syn, alerted, status = rows[0]
    return {"stored": True, "synthetic": bool(is_syn),
            "alerted": bool(alerted), "status": status}


def _record(target: str, responded: bool, key: str, detail: str) -> None:
    execute_query(_CREATE)
    execute_query(
        "INSERT INTO canary_runs (brand, target, responded, answered, lead_key, detail) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (_BRAND, target, responded, "manual", key, detail[:500]))


def _alert_red(target: str, key: str, detail: str) -> bool:
    """A red canary means the live funnel is dropping leads RIGHT NOW. Page loudly."""
    resend = (os.getenv("RESEND_API_KEY") or "").strip()
    to_addr = (os.getenv("LEAD_ALERT_TO") or "michael@automotiveintelligence.io").strip()
    frm = os.getenv("LEAD_ALERT_FROM", "AVO <cmo@mail.automotiveintelligence.io>")
    if not resend:
        logger.error("[canary] RED but RESEND_API_KEY missing -- cannot page: %s", detail)
        return False
    try:
        r = requests.post(
            _RESEND, timeout=15,
            headers={"Authorization": f"Bearer {resend}", "Content-Type": "application/json"},
            json={"from": frm, "to": [to_addr],
                  "subject": f"[FUNNEL CANARY RED] AIPG lead path failed ({target})",
                  "html": f"<p><b>The AIPG lead funnel canary did NOT land.</b> A real lead "
                          f"submitted right now would likely be lost.</p><ul>"
                          f"<li>target: {target}</li><li>key: {key}</li>"
                          f"<li>detail: {detail}</li></ul>"
                          f"<p>Paid spend stays gated until this is green.</p>"})
        return r.ok
    except requests.RequestException:
        logger.exception("[canary] RED alert email failed")
        return False


def run_canary(*, commit: bool = True) -> Dict[str, Any]:
    """One canary run. Submit synthetic -> verify durable row -> score responded vs
    answered -> record -> page loudly if red. Returns a receipt for state logging."""
    payload = _synthetic_payload()
    key = payload["idempotency_key"]
    sub = _submit(payload)
    target = sub["target"]
    check = _verify_durable(key)

    # responded = the lead is DURABLE in the system of record AND the alert path fired.
    # (For a synthetic, lead_store marks alerted via the 'synthetic' rail -- proving the
    # rail is wired without paging a human.)
    responded = bool(check.get("stored") and check.get("alerted"))
    detail = f"target={target} ack={sub.get('ack')} verify={check}"

    if commit:
        _record(target, responded, key, detail)
    paged = False
    if not responded:
        logger.error("[canary] RED: %s", detail)
        if commit:
            paged = _alert_red(target, key, detail)

    return {"ok": responded, "brand": _BRAND, "target": target,
            "responded": responded, "answered": "manual (human mystery-shop owns this)",
            "key": key, "verify": check, "paged_on_fail": paged}


def latest_canary(brand: str = _BRAND) -> Optional[Dict[str, Any]]:
    """Most recent canary run (feeds #17 absence alerting). None if never run."""
    rows = fetch_all(
        "SELECT responded, target, detail, EXTRACT(EPOCH FROM (NOW()-ran_at)) "
        "FROM canary_runs WHERE brand=%s ORDER BY ran_at DESC LIMIT 1", (brand,))
    if not rows:
        return None
    responded, target, detail, age_s = rows[0]
    return {"responded": bool(responded), "target": target,
            "detail": detail, "age_seconds": float(age_s or 0)}


def green_streak(brand: str = _BRAND, hours: int = 48) -> Dict[str, Any]:
    """The 48h-green proof the spend gate reads: over the window, how many runs, how many
    green, and were they against the REAL form. Fail-closed: all_green is True only with
    real runs and zero reds."""
    rows = fetch_all(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE responded), "
        "COUNT(*) FILTER (WHERE responded AND target='form'), "
        "MIN(ran_at), MAX(ran_at) "
        "FROM canary_runs WHERE brand=%s AND ran_at > NOW() - make_interval(hours => %s)",
        (brand, hours))
    total, green, green_form, first_ran, last_ran = rows[0] if rows else (0, 0, 0, None, None)
    total, green, green_form = int(total or 0), int(green or 0), int(green_form or 0)
    return {"brand": brand, "hours": hours, "total": total, "green": green,
            "reds": total - green, "green_against_real_form": green_form,
            "first_ran": str(first_ran) if first_ran else None,
            "last_ran": str(last_ran) if last_ran else None,
            "all_green": total > 0 and green == total,
            "gate_ready": green_form > 0 and green == total and total > 0}
