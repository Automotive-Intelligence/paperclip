"""services/cmo_daily_email.py — CMO Daily email (7:00 AM CT cron)

Per File #58 (B&T handoff from CMO autonomy spec). Michael delegated the
marketing gate to the CMO; this email is his ONCE-A-DAY interface to that
delegation. He inspects, not approves. Silence = trust the gate.

What this reports (rewired 2026-08-02)
--------------------------------------
The brief used to render from a hand-maintained avo-telemetry/cmo_daily_state.json
that went stale on 2026-06-24, so it told Michael "nothing shipped" while the
Railway Slipstream engine auto-published all weekend. It now reads REALITY:

  • "Shipped (auto)"      = blog posts merged to each brand's Next.js repo `main`
                            in the lookback window + social posts distributed
                            (services/cmo_shipped -> GitHub commits + social_registry).
  • "Held / awaiting you" = OPEN blog PRs (a human-merge WD post, or a gate HOLD),
                            not chores.
  • "Decisions for you"   = ONLY truly Michael-only calls, each with a default that
                            happens on silence. Empty by default (silence = trust).
  • Reply-To + override    = the brief is replyable; a reply is filed as a CMO
                            override in cmo_state.md (services/cmo_override).

The hand-maintained state.json is now an OPTIONAL editorial overlay (headline /
cmo_note / decisions) and is ignored when stale, so it can never again inject a
false status.

Schedule: APScheduler 7:00 AM America/Chicago daily.
Manual trigger: POST /admin/cmo-daily-email-now (Bearer auth).
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
# Reply-To points at a REAL inbox Michael reads (the connected `avi` Gmail,
# michael@automotiveintelligence.io — same brand family as the sender). A reply
# there is filed as a CMO override by services/cmo_override.
_DEFAULT_REPLY_TO = "michael@automotiveintelligence.io"
_REQUEST_TIMEOUT = 30

# Editorial overlay is trusted only if its date is within this many days of today;
# otherwise it is stale and ignored (it can never re-inject a false status).
_OVERLAY_FRESH_DAYS = 2


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


# ── State assembly (reality first, editorial overlay second) ─────────────────


def _reply_to() -> str:
    return (os.environ.get("CMO_DAILY_REPLY_TO") or _DEFAULT_REPLY_TO).strip()


def _real_brands(today_iso: str) -> List[Dict[str, Any]]:
    """Per-brand shipped/held reality from the live engine output. Never returns
    the false 'nothing shipped' placeholder — on a data failure a brand carries
    signal_ok=False and renders 'engine signal unavailable'."""
    from services import cmo_shipped
    rows = cmo_shipped.collect()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            **r,
            "shipped_lines": cmo_shipped.shipped_lines(r),
            "held_lines": cmo_shipped.held_lines(r),
        })
    return out


def _auto_headline(brands: List[Dict[str, Any]]) -> str:
    posts = sum(b.get("post_count", 0) for b in brands)
    social = sum(b.get("social", 0) for b in brands)
    shipped_brands = sum(1 for b in brands if b.get("post_count") or b.get("social"))
    if not (posts or social):
        stale = any(not b.get("signal_ok", True) for b in brands)
        if stale:
            return "Engine signal unavailable — verify Slipstream + SLIPSTREAM_GH_TOKEN"
        return "Quiet window — no new posts this cycle; gate green"
    bits = []
    if posts:
        bits.append(f"{posts} blog post{'s' if posts != 1 else ''}")
    if social:
        bits.append(f"{social} social post{'s' if social != 1 else ''}")
    return f"{' + '.join(bits)} shipped across {shipped_brands} brand{'s' if shipped_brands != 1 else ''} — auto-published, gate green"


def _auto_cmo_note(brands: List[Dict[str, Any]]) -> str:
    unavailable = [b["name"] for b in brands if not b.get("signal_ok", True)]
    base = (
        "Numbers are read live from each brand's repo `main` (blog posts merged) "
        "and the social registry (posts distributed), not a status file. The gate "
        "held everything you see; nothing below needs your approval."
    )
    if unavailable:
        base += (
            " NOTE: could not read the engine signal for "
            + ", ".join(unavailable)
            + " — that is a plumbing gap, not a claim that nothing shipped."
        )
    return base


def _fresh_overlay(today_iso: str) -> Dict[str, Any]:
    """Optional human editorial overlay (headline / cmo_note / decisions) from
    cmo_daily_state.json. Ignored entirely if missing or stale, so a forgotten
    state file can never re-inject last month's content."""
    try:
        raw = _fetch_telemetry_path("cmo_daily_state.json")
        if not raw.strip():
            return {}
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.info("[cmo-daily] overlay read failed (%s); reality-only", e)
        return {}
    overlay_date = (data.get("date") or "").strip()
    try:
        d0 = datetime.date.fromisoformat(overlay_date)
        d1 = datetime.date.fromisoformat(today_iso)
        if (d1 - d0).days > _OVERLAY_FRESH_DAYS:
            logger.info("[cmo-daily] overlay stale (%s); ignoring editorial fields", overlay_date)
            return {}
    except (ValueError, TypeError):
        return {}
    out: Dict[str, Any] = {}
    for k in ("headline", "cmo_note"):
        if data.get(k):
            out[k] = data[k]
    decisions = data.get("decisions")
    if isinstance(decisions, list) and decisions:
        out["decisions"] = _coerce_decisions(decisions)
    elif isinstance(data.get("needs_michael"), list) and data["needs_michael"]:
        # Legacy chore list -> minimal decision shape (default: CMO proceeds).
        out["decisions"] = [
            {"decision": str(x),
             "default": "CMO proceeds on the standing default.",
             "action": ""}
            for x in data["needs_michael"]
        ]
    return out


def _coerce_decisions(items: List[Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in items:
        if isinstance(it, dict):
            out.append({
                "decision": str(it.get("decision") or it.get("what") or "").strip(),
                "default": str(it.get("default") or "").strip(),
                "action": str(it.get("action") or "").strip(),
            })
        else:
            out.append({"decision": str(it).strip(), "default": "", "action": ""})
    return [d for d in out if d["decision"]]


def _load_state(today_iso: str) -> Dict[str, Any]:
    """Assemble the brief: live reality for the brand table, an optional (fresh)
    editorial overlay for headline / note / decisions."""
    try:
        brands = _real_brands(today_iso)
    except Exception as e:  # noqa: BLE001 - never fail the send on a data hiccup
        logger.error("[cmo-daily] reality collection failed: %s", e, exc_info=True)
        brands = []
    overlay = _fresh_overlay(today_iso)
    return {
        "date": today_iso,
        "headline": overlay.get("headline") or _auto_headline(brands),
        "cmo_note": overlay.get("cmo_note") or _auto_cmo_note(brands),
        "brands": brands,
        "decisions": overlay.get("decisions", []),
    }


# ── HTML rendering ───────────────────────────────────────────────────────────


def _li(items: Optional[List[str]], empty: str = "—") -> str:
    if not items:
        return f"<span style='color:#aaa'>{empty}</span>"
    return "<br>".join(f"• {x}" for x in items)


def _brand_row(b: Dict[str, Any]) -> str:
    return (
        f"<tr>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:13px'>"
        f"<b>{b.get('name','?')}</b><br>"
        f"<span style='color:#888;font-size:11px'>{_AUTONOMY_LABEL.get(b.get('autonomy'),'?')}</span></td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:18px;text-align:center'>{b.get('light','')}</td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:12px'>{_li(b.get('shipped_lines'), 'quiet this cycle')}</td>"
        f"<td style='padding:8px 12px;border:1px solid #444;font-size:12px'>{_li(b.get('held_lines'), 'nothing waiting')}</td>"
        f"</tr>"
    )


def _decision_html(d: Dict[str, str]) -> str:
    parts = [f"<div style='font-weight:600;font-size:13px'>{d.get('decision','')}</div>"]
    if d.get("default"):
        parts.append(
            f"<div style='font-size:12px;color:#555'>If you stay silent: {d['default']}</div>"
        )
    if d.get("action"):
        parts.append(f"<div style='font-size:12px;color:#0071e3'>{d['action']}</div>")
    return (
        "<li style='margin-bottom:10px;list-style:none;border-left:3px solid #e0a800;"
        "padding-left:10px'>" + "".join(parts) + "</li>"
    )


def _build_html(state: Dict[str, Any], today_iso: str) -> str:
    headline = state.get("headline", "All brands stable")
    cmo_note = state.get("cmo_note", "")
    brands = state.get("brands", []) or []
    decisions = state.get("decisions", []) or []
    reply_to = _reply_to()
    decisions_html = (
        "".join(_decision_html(d) for d in decisions)
        if decisions
        else "<li style='color:#888;list-style:none'>Nothing needs you today. Go sell cars.</li>"
    )
    return f"""
<div style="font-family:-apple-system,sans-serif;max-width:760px;background:#fff;color:#111;padding:24px">
<h1 style="font-size:18px;margin:0 0 4px">CMO Daily — {today_iso}</h1>
<div style="font-size:12px;color:#888;margin-bottom:18px">Your marketing org ran itself. You sold cars. Here's what shipped.</div>

<div style="background:#eef6ff;border-left:4px solid #0071e3;padding:14px 18px;margin-bottom:20px">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#0071e3;margin-bottom:6px">HEADLINE</div>
<div style="font-size:14px;font-weight:600">{headline}</div>
</div>

<table style="border-collapse:collapse;width:100%;margin-bottom:20px">
<thead><tr style="background:#f6f6f6">
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">Brand</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px">Status</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">Shipped (auto)</th>
<th style="padding:8px 12px;border:1px solid #444;font-size:11px;text-align:left">Held / awaiting you</th>
</tr></thead>
<tbody>
{''.join(_brand_row(b) for b in brands)}
</tbody>
</table>

<div style="background:#f9f9f9;border:1px solid #eee;padding:14px 18px;margin-bottom:20px">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#888;margin-bottom:6px">CMO note</div>
<div style="font-size:13px">{cmo_note}</div>
</div>

<h2 style="font-size:14px;margin:0 0 10px">Decisions for you <span style="font-weight:400;color:#888;font-size:12px">(silence = the default happens)</span></h2>
<ul style="font-size:13px;padding-left:0;margin-bottom:20px">
{decisions_html}
</ul>

<div style="background:#fffbe6;border-left:4px solid #e0a800;padding:12px 16px;margin-bottom:20px">
<div style="font-size:13px"><b>How to override:</b> reply to this email. It reaches <b>{reply_to}</b> (an inbox I monitor) and I file your reply as a CMO override in cmo_state.md, which the CMO reads at the start of every session. Silence means you trust the gate, and it keeps running.</div>
</div>

<hr style="border:0;border-top:1px solid #eee;margin:20px 0">
<div style="font-size:11px;color:#aaa">CMO Daily · all brands · gates: hero-metrics · voice · claims-ledger · mechanics · {today_iso}</div>
</div>
"""


def _build_text(state: Dict[str, Any], today_iso: str) -> str:
    headline = state.get("headline", "All brands stable")
    cmo_note = state.get("cmo_note", "")
    brands = state.get("brands", []) or []
    decisions = state.get("decisions", []) or []
    reply_to = _reply_to()
    lines = [f"CMO DAILY — {today_iso}", "", f"HEADLINE: {headline}", ""]
    for b in brands:
        lines.append(f"{b.get('light','')} {b.get('name','?')} ({_AUTONOMY_LABEL.get(b.get('autonomy'),'?')})")
        shipped = b.get("shipped_lines") or []
        held = b.get("held_lines") or []
        lines.append(f"   shipped: {'; '.join(shipped) if shipped else 'quiet this cycle'}")
        lines.append(f"   awaiting you: {'; '.join(held) if held else 'nothing'}")
    lines += ["", f"CMO NOTE: {cmo_note}", "", "DECISIONS FOR YOU (silence = the default happens):"]
    if decisions:
        for d in decisions:
            lines.append(f"  - {d.get('decision','')}")
            if d.get("default"):
                lines.append(f"      if silent: {d['default']}")
            if d.get("action"):
                lines.append(f"      action: {d['action']}")
    else:
        lines.append("  - Nothing needs you today. Go sell cars.")
    lines += [
        "",
        f"TO OVERRIDE: reply to this email. It reaches {reply_to} (an inbox I monitor) "
        "and I file your reply as a CMO override in cmo_state.md. Silence = trust the gate.",
    ]
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
        "reply_to": _reply_to(),
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
                "reply_to": payload["reply_to"],
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
