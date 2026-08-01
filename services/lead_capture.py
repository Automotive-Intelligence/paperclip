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
RESEND_API = "https://api.resend.com/emails"
ALERT_FROM = os.getenv("LEAD_ALERT_FROM", "AVO Leads <cmo@mail.automotiveintelligence.io>")
# Default MUST be a live, monitored inbox. The old default (michael@worshipdigital.co)
# is a hole: mail.worshipdigital.co is `failed` in Resend and an audit found zero mail
# delivered there in 180 days (deliverable 155). michael@automotiveintelligence.io is
# the owner's real inbox and mail.automotiveintelligence.io is Resend-verified.
ALERT_TO = os.getenv("LEAD_ALERT_TO", "michael@automotiveintelligence.io")


def _alert_email(brand: str, lead: dict) -> bool:
    """Primary alert rail: email. A lead is worth waking someone for."""
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key:
        return False
    rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#666'>{k}</td>"
        f"<td style='padding:4px 0'><b>{v or '-'}</b></td></tr>"
        for k, v in lead.items())
    html = (f"<p>A lead came in that the CRM could not accept. <b>Work it by hand.</b></p>"
            f"<table>{rows}</table>"
            f"<p style='color:#666;font-size:12px'>brand: {brand}. This email is the lead "
            f"record until it is entered manually.</p>")
    try:
        r = requests.post(RESEND_API, timeout=15,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"from": ALERT_FROM, "to": [ALERT_TO],
                                "subject": f"[LEAD - {brand}] {lead.get('name') or 'unknown'} "
                                           f"({lead.get('phone') or 'no phone'})",
                                "html": html})
        if r.status_code >= 300:
            logging.error("[lead_capture] email failed %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception:
        logging.exception("[lead_capture] alert email raised")
        return False


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
    emailed = _alert_email(brand, lead)
    issued = False if emailed else _alert_issue(brand, lead)  # issue only as backup
    alerted = bool(emailed or issued)
    # FAIL CLOSED (funnel-standard item 5, deliverable 156): a log-line receipt is a
    # durability floor, NOT "a human was told". ok is True ONLY when a human was
    # actually alerted (email or issue). The caller must degrade its success message
    # / return 502 when ok is False, never report success on a receipt-only capture.
    return {"ok": alerted, "captured": "receipt",
            "alerted": alerted,
            "via": "email" if emailed else ("issue" if issued else "receipt-only")}
