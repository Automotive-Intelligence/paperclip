"""Lead-never-lost capture (deliverable 152 companion).

A brand site's primary CRM write (e.g. AIPG -> GHL) can fail: expired key,
vendor outage, API version drift. When it does, the lead MUST NOT be turned
away with "call us" — that is a paid click discarded. This endpoint is the
fallback of record: it logs the lead as a durable receipt and raises the
standing alert rail (GitHub issue -> email) so a human can work it by hand
within minutes.

Contract: returns ok=True only when the lead is genuinely captured somewhere.
"""
import os, json, logging
import requests

GH_TOKEN = os.getenv("SLIPSTREAM_GH_TOKEN", "")
ALERT_REPO = os.getenv("LEAD_ALERT_REPO", "Automotive-Intelligence/paperclip")


def _alert_issue(brand: str, lead: dict) -> bool:
    if not GH_TOKEN:
        return False
    body = "\n".join([
        f"**A lead came in that the primary CRM could not accept. Work it by hand.**",
        "",
        f"- brand: `{brand}`",
        f"- name: {lead.get('name','')}",
        f"- phone: {lead.get('phone','')}",
        f"- email: {lead.get('email','')}",
        f"- trade: {lead.get('trade','')}",
        f"- source: {lead.get('source','')}",
        f"- message: {lead.get('message','')}",
        "",
        "Primary CRM write failed (see site logs). This issue IS the lead record until it is entered manually.",
    ])
    try:
        r = requests.post(
            f"https://api.github.com/repos/{ALERT_REPO}/issues", timeout=15,
            headers={"Authorization": f"Bearer {GH_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            json={"title": f"[LEAD - {brand}] {lead.get('name','unknown')} ({lead.get('phone','no phone')})",
                  "body": body, "labels": ["lead", "needs-manual-entry"]})
        if r.status_code >= 300:
            logging.error("[lead_capture] issue failed %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception:
        logging.exception("[lead_capture] alert issue raised an exception")
        return False


def capture(payload: dict) -> dict:
    """Durable fallback capture. Receipt first (always), then alert rail."""
    brand = str(payload.get("brand") or "unknown")
    lead = {k: str(payload.get(k) or "")[:300] for k in
            ("name", "phone", "email", "trade", "message", "source")}
    # The receipt is the floor: even if GitHub is down, the lead exists in logs.
    logging.warning("[LEAD CAPTURED - FALLBACK] %s", json.dumps({"brand": brand, **lead}))
    alerted = _alert_issue(brand, lead)
    return {"ok": True, "captured": "receipt", "alerted": alerted}
