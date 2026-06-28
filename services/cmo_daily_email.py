"""services/cmo_daily_email.py — CMO Daily email (7:00 AM CT cron)

Per File #58 (B&T handoff from CMO autonomy spec). Michael delegated the
marketing gate to the CMO; this email is his ONCE-A-DAY interface to that
delegation. He inspects, not approves. Silence = trust the gate.

Sources:
  • state                 = avo-telemetry/cmo_daily_state.json via GitHub
                            Contents API (already-authenticated path used
                            by flag_router). Falls back to a "standing up"
                            payload if the file is missing.
  • brand-by-brand status = ledger entries written by producing routines
                            + the CMO inspection step (file 57 ledgers).
  • send                  = Resend, sender cmo@mail.automotiveintelligence.io
                            (subdomain already verified for pitleader@).

Schedule: APScheduler 7:00 AM America/Chicago daily.
Manual trigger: POST /admin/cmo-daily-email-now (Bearer auth).

Mirrors the standalone scripts/cmo_daily_email.py in avo-telemetry — that
script stays as the human-runnable / git-tracked source of truth for the
template; this module is the runtime path.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from services.flag_router import _fetch_telemetry_path  # auth'd reader

logger = logging.getLogger(__name__)


_RESEND_API = "https://api.resend.com/emails"
_DEFAULT_FROM = "Michael's CMO <cmo@mail.automotiveintelligence.io>"
_DEFAULT_TO = "michael@worshipdigital.co"
_REQUEST_TIMEOUT = 30


_AUTONOMY_LABEL = {
    "auto": "Full auto · CMO-gated",
    "partial": "Partial auto",
    "oversight": "Oversight only",
}


def _today_iso() -> str:
    """ISO date in America/Chicago — CMO Daily is keyed to local Texas day."""
    import zoneinfo
    cdt = datetime.datetime.now(zoneinfo.ZoneInfo("America/Chicago"))
    return cdt.date().isoformat()


def _fallback_state(today_iso: str) -> Dict[str, Any]:
    """Standing-up brief used when cmo_daily_state.json hasn't been written
    yet (e.g., producing routines aren't all live). Mirrors the script's
    hard-coded fallback verbatim so the morning email stays consistent."""
    return {
        "date": today_iso,
        "headline": "CMO operating system standing up — gates + claims ledgers live, producing routines being wired",
        "cmo_note": (
            "Engine, gates, and the 5 claims ledgers are in place. Producing + "
            "auto-publish routines are being wired into Railway (file 58). Until "
            "then this brief reports status; once live it reports what shipped."
        ),
        "brands": [
            {"brand": "Worship Digital", "autonomy": "auto", "light": "🟢",
             "shipped": [], "held": [], "blocked": ["Producing routine not yet cron'd"]},
            {"brand": "Automotive Intelligence", "autonomy": "auto", "light": "🟢",
             "shipped": [], "held": [], "blocked": ["Producing routine not yet cron'd; logo missing gates creative"]},
            {"brand": "Build Agent Empire", "autonomy": "auto", "light": "🟢",
             "shipped": [], "held": [], "blocked": ["Producing routine not yet cron'd; brand-kit assets missing gates creative"]},
            {"brand": "AI Phone Guy", "autonomy": "partial", "light": "🟡",
             "shipped": [], "held": [], "blocked": ["Logo/hex missing (creative); no entity = no GBP (local SEO + reviews)"]},
            {"brand": "Book'd", "autonomy": "oversight", "light": "⚪",
             "shipped": [], "held": [], "blocked": ["Oversight only — recommendations to Ryan; Zernio 0 channels connected"]},
        ],
        "needs_michael": [
            "Resolve AIPG entity (unblocks Google Business Profile + local-SEO/review play)",
            "Drop AIPG + Book'd brand-kit files (logo/hex) into brand-kit folders",
            "One-time guarantee-language sign-off per brand that publishes a guarantee",
        ],
    }


def _load_state(today_iso: str) -> Dict[str, Any]:
    """Pull state from avo-telemetry/cmo_daily_state.json via the same
    authenticated GitHub Contents API the flag_router uses. Falls back to
    the standing-up payload on any failure."""
    try:
        raw = _fetch_telemetry_path("cmo_daily_state.json")
        if not raw.strip():
            logger.warning("[cmo-daily] cmo_daily_state.json empty; using fallback")
            return _fallback_state(today_iso)
        return json.loads(raw)
    except Exception as e:
        logger.warning("[cmo-daily] state fetch failed (%s); using fallback", e)
        return _fallback_state(today_iso)


def _li(items: Optional[List[str]], empty: str = "—") -> str:
    if not items:
        return f"<span style='color:#aaa'>{empty}</span>"
    return "<br>".join(f"• {x}" for x in items)


def _brand_row(b: Dict[str, Any]) -> str:
    return (
        f"<tr>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:13px'>"
        f"<b>{b.get('brand','?')}</b><br>"
        f"<span style='color:#888;font-size:11px'>{_AUTONOMY_LABEL.get(b.get('autonomy'),'?')}</span></td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:18px;text-align:center'>{b.get('light','')}</td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:12px'>{_li(b.get('shipped'), 'nothing shipped')}</td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:12px'>{_li(b.get('held'), 'nothing held')}</td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:12px'>{_li(b.get('blocked'))}</td>"
        f"</tr>"
    )


def _build_html(state: Dict[str, Any], today_iso: str) -> str:
    headline = state.get("headline", "All brands stable")
    cmo_note = state.get("cmo_note", "")
    brands = state.get("brands", []) or []
    needs = state.get("needs_michael", []) or []
    return f"""
<div style="font-family:-apple-system,sans-serif;max-width:760px;background:#fff;color:#111;padding:24px">
<h1 style="font-size:18px;margin:0 0 4px">CMO Daily — {today_iso}</h1>
<div style="font-size:12px;color:#888;margin-bottom:18px">Your marketing org ran itself. You sold cars. Here's the gate report.</div>

<div style="background:#eef6ff;border-left:4px solid #0071e3;padding:14px 18px;margin-bottom:20px">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#0071e3;margin-bottom:6px">HEADLINE</div>
<div style="font-size:14px;font-weight:600">{headline}</div>
</div>

<table style="border-collapse:collapse;width:100%;margin-bottom:20px">
<thead><tr style="background:#f6f6f6">
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">Brand</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px">Status</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">Shipped (auto)</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">CMO held</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">Blocked</th>
</tr></thead>
<tbody>
{''.join(_brand_row(b) for b in brands)}
</tbody>
</table>

<div style="background:#f9f9f9;border:1px solid #eee;padding:14px 18px;margin-bottom:20px">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#888;margin-bottom:6px">CMO note</div>
<div style="font-size:13px">{cmo_note}</div>
</div>

<h2 style="font-size:14px;margin:0 0 10px">Needs Michael (only you can do these)</h2>
<ul style="font-size:13px;padding-left:20px;margin-bottom:20px">
{''.join(f'<li style="margin-bottom:6px">{n}</li>' for n in needs) or '<li style="color:#aaa">Nothing today. Go sell cars.</li>'}
</ul>

<div style="background:#fffbe6;border-left:4px solid #e0a800;padding:12px 16px;margin-bottom:20px">
<div style="font-size:13px"><b>Reminder:</b> you are inspecting the CMO, not approving assets. If anything above looks wrong, reply and I'll pull it. Silence means you trust the gate, and it keeps running.</div>
</div>

<hr style="border:0;border-top:1px solid #eee;margin:20px 0">
<div style="font-size:11px;color:#aaa">CMO Daily · all 5 brands · gates: hero-metrics · voice · claims-ledger · mechanics · {today_iso}</div>
</div>
"""


def _build_text(state: Dict[str, Any], today_iso: str) -> str:
    headline = state.get("headline", "All brands stable")
    cmo_note = state.get("cmo_note", "")
    brands = state.get("brands", []) or []
    needs = state.get("needs_michael", []) or []
    lines = [f"CMO DAILY — {today_iso}", "", f"HEADLINE: {headline}", ""]
    for b in brands:
        lines.append(f"{b.get('light','')} {b.get('brand','?')} ({_AUTONOMY_LABEL.get(b.get('autonomy'),'?')})")
        lines.append(f"   shipped: {', '.join(b.get('shipped') or []) or 'nothing'}")
        lines.append(f"   held:    {', '.join(b.get('held') or []) or 'nothing'}")
        lines.append(f"   blocked: {', '.join(b.get('blocked') or []) or '—'}")
    lines += ["", f"CMO NOTE: {cmo_note}", "", "NEEDS MICHAEL:"]
    lines += [f"  - {n}" for n in needs] or ["  - nothing today"]
    lines += ["", "Reply to override any asset. Silence = trust the gate."]
    return "\n".join(lines)


def send_cmo_daily() -> Dict[str, Any]:
    """Build + send the CMO Daily email via Resend. Returns a summary dict."""
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not api_key:
        return {"status": "skip", "reason": "RESEND_API_KEY not set"}

    today_iso = _today_iso()
    state = _load_state(today_iso)
    headline = state.get("headline", "All brands stable")
    subject = f"CMO Daily — {today_iso} — {headline[:80]}"

    payload = {
        "from": os.environ.get("CMO_DAILY_FROM", _DEFAULT_FROM),
        "to": [os.environ.get("CMO_DAILY_TO", _DEFAULT_TO)],
        "subject": subject,
        "html": _build_html(state, today_iso),
        "text": _build_text(state, today_iso),
    }
    try:
        r = requests.post(
            _RESEND_API,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "avo-cmo-daily/1.0",
            },
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )
        if r.ok:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "status": "sent",
                "date": today_iso,
                "to": payload["to"][0],
                "subject": subject,
                "id": body.get("id"),
            }
        logger.error("[cmo-daily] Resend http=%s body=%s", r.status_code, r.text[:300])
        return {"status": "failed", "http": r.status_code, "body": r.text[:300]}
    except Exception as e:
        logger.exception("[cmo-daily] send raised: %s", e)
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def run_daily() -> Dict[str, Any]:
    """APScheduler entry point — wraps send_cmo_daily with safe-failure logging."""
    try:
        result = send_cmo_daily()
        logger.info("[cmo-daily] result: %s", result)
        return result
    except Exception as e:
        logger.exception("[cmo-daily] run_daily crashed: %s", e)
        return {"status": "crashed", "error": f"{type(e).__name__}: {e}"}
