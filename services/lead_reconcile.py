"""services/lead_reconcile.py -- funnel standard item #7: daily reconciliation.

Webhook-only delivery is not fail-closed (the vendors say so): Meta drops unacknowledged
webhooks after 36 hours, GHL/Stripe do not guarantee exactly-once. The ONLY control that
catches a silent drop is a job that pulls the CRM's own count and diffs it against our
system of record (services/lead_store), and ALERTS on any delta.

For AIPG the source is the website form -> lead_store (system of record) -> GHL (CRM). A
mismatch means a lead reached one side and not the other:
  store > CRM  -> a stored lead never made it to GHL (a dead-letter that needs working).
  CRM  > store -> a lead reached GHL by a path that bypassed the system of record
                  (e.g. a Meta lead-ad webhook straight into GHL) -- exactly the drop the
                  standard warns about.

FAIL CLOSED: if the CRM cannot be read, we CANNOT prove the funnel is whole, so we alert
"could not verify" rather than pass silently. Every external effect is a module seam.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

from services.lead_store import recent_count

logger = logging.getLogger(__name__)

_GHL_SEARCH = "https://services.leadconnectorhq.com/contacts/search"
_RESEND = "https://api.resend.com/emails"
_TAG = "website-lead"


def _ghl_recent_count(hours: int) -> Optional[int]:
    """Count GHL contacts tagged `website-lead` added in the trailing window. Returns
    None if GHL cannot be read (-> reconcile fails closed and alerts)."""
    key = (os.getenv("GHL_API_KEY") or "").strip()
    loc = (os.getenv("GHL_LOCATION_ID") or "").strip()
    if not key or not loc:
        logger.error("[reconcile] GHL creds missing; cannot read CRM")
        return None
    import time
    since_ms = int((time.time() - hours * 3600) * 1000)
    try:
        r = requests.post(
            _GHL_SEARCH, timeout=25,
            headers={"Authorization": f"Bearer {key}", "Version": "2021-07-28",
                     "Content-Type": "application/json"},
            json={"locationId": loc, "pageLimit": 100,
                  "filters": [{"field": "tags", "operator": "contains", "value": _TAG}],
                  "sort": [{"field": "dateAdded", "direction": "desc"}]})
        if not r.ok:
            logger.error("[reconcile] GHL search %s: %s", r.status_code, r.text[:160])
            return None
        contacts = (r.json() or {}).get("contacts", []) or []
    except (requests.RequestException, ValueError):
        logger.exception("[reconcile] GHL search failed")
        return None

    # Client-side window filter (GHL date operators are inconsistent across accounts;
    # counting client-side is robust and the volume is small).
    n = 0
    for c in contacts:
        added = c.get("dateAdded") or c.get("dateUpdated") or ""
        # dateAdded is ISO8601; cheap epoch-ms comparison via fromisoformat when possible.
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(str(added).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.timestamp() * 1000 >= since_ms:
                n += 1
        except ValueError:
            n += 1   # unparseable date -> count it (fail toward flagging, not hiding)
    return n


def _alert(subject: str, html: str) -> bool:
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    to_addr = (os.getenv("LEAD_ALERT_TO") or "michael@automotiveintelligence.io").strip()
    frm = os.getenv("LEAD_ALERT_FROM", "AVO <cmo@mail.automotiveintelligence.io>")
    if not key:
        logger.error("[reconcile] RESEND_API_KEY missing; cannot alert: %s", subject)
        return False
    try:
        r = requests.post(_RESEND, timeout=15,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json={"from": frm, "to": [to_addr], "subject": subject, "html": html})
        return r.ok
    except requests.RequestException:
        logger.exception("[reconcile] alert failed")
        return False


def reconcile(brand: str = "aipg", hours: int = 24, *, commit: bool = True) -> Dict[str, Any]:
    """Diff the system of record against the CRM for the window. Alert on any delta or on
    an unreadable CRM (fail closed). Returns a receipt for state logging."""
    store_n = recent_count(brand, hours, synthetic=False)
    crm_n = _ghl_recent_count(hours)

    if crm_n is None:
        detail = f"CRM unreadable; store has {store_n} {brand} lead(s) in {hours}h"
        alerted = _alert(f"[Reconcile] {brand.upper()} CRM unreadable -- cannot verify funnel",
                         f"<p>Reconciliation could NOT read GHL, so a silent drop cannot be "
                         f"ruled out.</p><p>{detail}</p>") if commit else False
        return {"ok": False, "brand": brand, "store": store_n, "crm": None,
                "delta": None, "alerted": alerted, "note": "CRM unreadable (fail closed)"}

    delta = store_n - crm_n
    if delta != 0:
        direction = ("stored-but-not-in-CRM (dead-letters to work)" if delta > 0
                     else "in-CRM-but-not-stored (a path bypassed the system of record)")
        alerted = _alert(
            f"[Reconcile] {brand.upper()} lead delta {delta:+d} in {hours}h",
            f"<p>System of record and CRM disagree over the last {hours}h.</p><ul>"
            f"<li>system of record (lead_store): <b>{store_n}</b></li>"
            f"<li>CRM (GHL, tag {_TAG}): <b>{crm_n}</b></li>"
            f"<li>delta: <b>{delta:+d}</b> -- {direction}</li></ul>") if commit else False
        return {"ok": False, "brand": brand, "store": store_n, "crm": crm_n,
                "delta": delta, "alerted": alerted}

    return {"ok": True, "brand": brand, "store": store_n, "crm": crm_n, "delta": 0,
            "note": "system of record and CRM agree"}
