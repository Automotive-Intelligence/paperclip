"""Morning Briefing — daily 8am CDT email/SMS to Michael.

Pulls last-24h activity from:
  - agent_logs in Postgres (every agent's run history)
  - Instantly campaigns (Tyler + Ryan — sends, opens, replies, BOX BOX)
  - HubSpot + GHL new contacts
  - Attio (Marcus enrollments)

Composes an HTML email and sends via Resend. Optional SMS headline via GHL.

Scheduled: 8am CDT daily via APScheduler.
"""

import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from services.database import fetch_all

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────

# Recipients — comma-separated BRIEFING_RECIPIENTS env override.
# Default adds salesdroid (CRO sweep inbox) alongside the primary AvI inbox.
BRIEFING_RECIPIENTS = [
    r.strip() for r in os.getenv(
        "BRIEFING_RECIPIENTS",
        "michael@automotiveintelligence.io,salesdroid@gmail.com",
    ).split(",") if r.strip()
]
# Backward-compat: BRIEFING_RECIPIENT (singular) overrides if set.
_legacy_single = os.getenv("BRIEFING_RECIPIENT", "").strip()
if _legacy_single:
    BRIEFING_RECIPIENTS = [_legacy_single]
BRIEFING_RECIPIENT = BRIEFING_RECIPIENTS[0]  # legacy callers
BRIEFING_SMS_TO = os.getenv("MICHAEL_PHONE", "")
BRIEFING_FROM = os.getenv(
    "BRIEFING_FROM",
    "Paperclip Briefing <briefing@mail.automotiveintelligence.io>",
)

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"
RESEND_BASE = "https://api.resend.com/emails"


# ── Agent role taxonomy ─────────────────────────────────────────────────────

# Map of agent_name -> (business, role) for structured reporting
AGENT_CATALOG = {
    # AI Phone Guy
    "tyler": ("AI Phone Guy", "Sales"),
    "alex": ("AI Phone Guy", "CEO"),
    "zoe": ("AI Phone Guy", "Marketing"),
    "jennifer": ("AI Phone Guy", "Customer Success"),
    "randy": ("AI Phone Guy", "RevOps"),
    "joshua": ("AI Phone Guy", "RevOps / Pit Wall"),

    # Calling Digital
    "marcus": ("Calling Digital", "Sales"),
    "dek": ("Calling Digital", "CEO"),
    "sofia": ("Calling Digital", "Marketing"),
    "carlos": ("Calling Digital", "Content"),
    "nova": ("Calling Digital", "Intelligence"),
    "brenda": ("Calling Digital", "RevOps"),

    # Automotive Intelligence
    "ryan_data": ("Automotive Intelligence", "Sales"),
    "michael_meta": ("Automotive Intelligence", "CEO"),
    "chase": ("Automotive Intelligence", "Content"),
    "phoenix": ("Automotive Intelligence", "Delivery"),
    "atlas": ("Automotive Intelligence", "Intelligence"),
    "darrell": ("Automotive Intelligence", "RevOps"),

    # Customer Advocate
    "sherry": ("Customer Advocate", "Retention"),
    "clint": ("Customer Advocate", "Expansion"),

    # Agent Empire
    "sterling": ("Agent Empire", "Strategy"),
    "wade": ("Agent Empire", "Operations"),
    "tammy_ae": ("Agent Empire", "Community"),
    "debra": ("Agent Empire", "Content"),

    # Executive
    "axiom": ("Executive", "CEO"),
    # coo_agent retired from dashboard 2026-06-24 — CRO chat absorbs the
    # agent-accountability function. Underlying scheduled job + cockpit
    # consumer still wired in app.py; B&T migration to follow.
}


# ── Data pullers ────────────────────────────────────────────────────────────

def pull_agent_activity_last_24h() -> Dict[str, Dict[str, Any]]:
    """Query agent_logs for every agent's activity in the last 24 hours.

    Returns dict keyed by agent_name with:
      - run_count: how many runs in the last 24h
      - last_run: timestamp of most recent run
      - last_log_preview: first 200 chars of the last output
    """
    try:
        rows = fetch_all(
            """
            SELECT agent_name, COUNT(*) as runs, MAX(created_at) as last_run,
                   (SELECT content FROM agent_logs al2
                    WHERE al2.agent_name = al.agent_name
                    ORDER BY created_at DESC LIMIT 1) as last_content
            FROM agent_logs al
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY agent_name
            ORDER BY last_run DESC
            """
        )
    except Exception as e:
        logger.warning(f"[Briefing] agent_logs query failed: {e}")
        return {}

    activity = {}
    for row in rows:
        agent_name, run_count, last_run, last_content = row
        activity[agent_name] = {
            "run_count": run_count,
            "last_run": last_run,
            "last_preview": (last_content or "")[:200].replace("\n", " "),
        }
    return activity


def pull_instantly_campaign_summary(api_key: str, campaign_id: str) -> Dict[str, Any]:
    """Pull a summary of campaign activity."""
    if not api_key or not campaign_id:
        return {"error": "not_configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    summary = {
        "total_leads": 0,
        "sent_24h": 0,
        "opens_24h": 0,
        "replies_24h": 0,
        "clicks_24h": 0,
        "lifetime_sent": 0,
        "lifetime_opens": 0,
        "lifetime_replies": 0,
        "lifetime_bounced": 0,
        "open_rate_pct": None,   # opens / sent * 100
        "reply_rate_pct": None,  # replies / sent * 100
        "deliverability_dead": False,  # >50 sent and 0 opens
        "box_box": [],  # leads with replies or clicks
        "dnfs": 0,      # bounced/error status
        "status": "unknown",
    }

    # Campaign status
    try:
        r = requests.get(f"{INSTANTLY_BASE}/campaigns/{campaign_id}",
            headers=headers, timeout=15)
        if r.status_code == 200:
            c = r.json()
            summary["status"] = {0: "Draft", 1: "Active", 2: "Paused", 3: "Completed"}.get(c.get("status"), "?")
            summary["sender"] = c.get("email_list", [])
    except Exception as e:
        logger.warning(f"[Briefing] campaign fetch failed: {e}")

    # Pull all leads (paginated)
    cursor = None
    for _ in range(15):
        body = {"campaign": campaign_id, "limit": 100}
        if cursor:
            body["starting_after"] = cursor
        try:
            r = requests.post(f"{INSTANTLY_BASE}/leads/list",
                headers=headers, json=body, timeout=20)
            if r.status_code != 200:
                break
            d = r.json()
            for lead in d.get("items", []):
                summary["total_leads"] += 1
                replies = lead.get("email_reply_count", 0) or 0
                opens = lead.get("email_open_count", 0) or 0
                clicks = lead.get("email_click_count", 0) or 0
                status = lead.get("status", 0)

                if replies > 0:
                    summary["box_box"].append({
                        "email": lead.get("email", ""),
                        "company": lead.get("company_name", ""),
                        "signal": f"REPLIED ({replies})",
                    })
                elif clicks > 0:
                    summary["box_box"].append({
                        "email": lead.get("email", ""),
                        "company": lead.get("company_name", ""),
                        "signal": f"CLICKED ({clicks})",
                    })
                if status in (-1, -2, -3):
                    summary["dnfs"] += 1
            cursor = d.get("next_starting_after")
            if not cursor:
                break
        except Exception as e:
            logger.warning(f"[Briefing] leads page failed: {e}")
            break

    # Lifetime analytics (correct v2 endpoint — GET, not POST)
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/campaigns/analytics",
            headers=headers,
            params={"campaign_id": campaign_id},
            timeout=20,
        )
        if r.status_code == 200:
            stats = r.json()
            if isinstance(stats, list) and stats:
                stats = stats[0]
            summary["lifetime_sent"] = stats.get("emails_sent_count", 0) or 0
            summary["lifetime_opens"] = stats.get("open_count", 0) or 0
            summary["lifetime_replies"] = stats.get("reply_count", 0) or 0
            summary["lifetime_bounced"] = stats.get("bounced_count", 0) or 0
            if summary["lifetime_sent"] > 0:
                summary["open_rate_pct"] = round(
                    100 * summary["lifetime_opens"] / summary["lifetime_sent"], 1
                )
                summary["reply_rate_pct"] = round(
                    100 * summary["lifetime_replies"] / summary["lifetime_sent"], 1
                )
            # Deliverability gutter check: enough volume to judge + zero engagement
            if summary["lifetime_sent"] >= 50 and summary["lifetime_opens"] == 0:
                summary["deliverability_dead"] = True
        else:
            logger.warning(f"[Briefing] analytics GET {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[Briefing] lifetime analytics pull failed: {e}")

    # Yesterday's per-day row (real 24h numbers from /analytics/daily)
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/campaigns/analytics/daily",
            headers=headers,
            params={"campaign_id": campaign_id},
            timeout=20,
        )
        if r.status_code == 200:
            rows = r.json() or []
            if isinstance(rows, list) and rows:
                yesterday_str = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
                # Match yesterday's row, fall back to most recent if not found
                row = next((x for x in rows if str(x.get("date", "")).startswith(yesterday_str)), rows[-1])
                summary["sent_24h"] = row.get("sent", row.get("emails_sent_count", 0)) or 0
                summary["opens_24h"] = row.get("opened", row.get("open_count", 0)) or 0
                summary["replies_24h"] = row.get("replies", row.get("reply_count", 0)) or 0
                summary["clicks_24h"] = row.get("clicks", row.get("click_count", 0)) or 0
        else:
            logger.warning(f"[Briefing] daily analytics GET {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[Briefing] daily analytics pull failed: {e}")

    return summary


# Morning-brief brand label → Twenty workspace business_key (tools/twenty.py).
# AIPG has no Twenty workspace (runs on GHL, no entity yet) so it maps to None
# and renders as an unwired row rather than fabricating zeros as real data.
_BRAND_TO_TWENTY_KEY = {
    "WD": "callingdigital",
    "AvI": "autointelligence",
    "AIPG": None,
    "Book'd": "bookd",
}


def pull_revenue_pipeline_summary(replies_pending_4h: int = 0) -> Dict[str, Any]:
    """CRO revenue layer — cross-brand pipeline state for the morning brief.

    Reads each brand's own Twenty workspace (multi-tenant; see tools/twenty.py
    _workspace_config — TWENTY_<BRAND>_API_KEY / _URL) and aggregates open-deal
    value, stall counts, and 24h booking/close activity. Brands without a
    provisioned workspace (AIPG today; Book'd until its Doppler secrets land)
    surface as unwired rows instead of zeros masquerading as data.

    Returns:
      pipeline_value:     sum of open-deal values across wired brands ($)
      deals_active:       all open (non-CUSTOMER) deals
      deals_stalled_7d:   open deals not touched in > 7 days
      replies_pending_4h: replies awaiting response > 4h — passed in from the
                          outreach reply queue (Instantly/Loops). 0 until a
                          reply-age feed exists; Instantly's lead payload has no
                          per-reply timestamp, so it can't be derived here.
      bookings_24h:       deals moved to MEETING in last 24h (stage proxy)
      closed_24h:         deals moved to CUSTOMER in last 24h (stage proxy)
      by_brand:           per-brand metrics dict (WD, AvI, AIPG, Book'd)
      wired:              True if at least one brand workspace was read
    """
    try:
        from tools.twenty import fetch_pipeline_summary
    except Exception as e:
        logger.warning(f"[Briefing] Twenty client import failed: {e}")
        return {
            "pipeline_value": 0, "deals_active": 0, "deals_stalled_7d": 0,
            "replies_pending_4h": replies_pending_4h, "bookings_24h": 0,
            "closed_24h": 0, "by_brand": {}, "wired": False,
        }

    now = datetime.now(timezone.utc)
    totals = {
        "pipeline_value": 0, "deals_active": 0,
        "deals_stalled_7d": 0, "bookings_24h": 0, "closed_24h": 0,
    }
    by_brand: Dict[str, Any] = {}
    any_wired = False

    for brand, business_key in _BRAND_TO_TWENTY_KEY.items():
        if business_key is None:
            by_brand[brand] = {**{k: 0 for k in totals}, "wired": False}
            continue
        try:
            b = fetch_pipeline_summary(business_key, now=now)
        except Exception as e:
            logger.warning(f"[Briefing] {brand} pipeline pull failed: {e}")
            b = {**{k: 0 for k in totals}, "wired": False}
        by_brand[brand] = b
        if b.get("wired"):
            any_wired = True
            for k in totals:
                totals[k] += b.get(k, 0)

    return {
        **totals,
        "replies_pending_4h": replies_pending_4h,
        "by_brand": by_brand,
        "wired": any_wired,
    }


# ── HTML Composer ───────────────────────────────────────────────────────────

def _agent_section(title: str, agents: List[str], activity: Dict[str, Dict]) -> str:
    """Build an HTML section for a group of agents."""
    rows = []
    for agent_name in agents:
        if agent_name in activity:
            a = activity[agent_name]
            last_run = a["last_run"].strftime("%H:%M UTC") if a.get("last_run") else "?"
            rows.append(
                f"<tr><td style='padding:4px 10px;'>✓ <b>{agent_name}</b></td>"
                f"<td style='padding:4px 10px;'>{a['run_count']} runs</td>"
                f"<td style='padding:4px 10px;color:#666;'>last: {last_run}</td></tr>"
            )
        else:
            rows.append(
                f"<tr style='background:#fff5f5;'><td style='padding:4px 10px;'>⚠ <b>{agent_name}</b></td>"
                f"<td style='padding:4px 10px;' colspan='2'><i>no runs in last 24h</i></td></tr>"
            )
    return (
        f"<h3 style='margin-top:20px;margin-bottom:6px;'>{title}</h3>"
        f"<table style='border-collapse:collapse;width:100%;font-size:13px;'>{''.join(rows)}</table>"
    )


def _cto_html(infra: Dict[str, Any]) -> str:
    """Render the Infrastructure (CTO) row for the morning brief."""
    status = infra.get("status", "unknown")
    icon = infra.get("icon", "⚪")
    count = infra.get("findings_count", 0)
    preview = infra.get("findings_preview", [])

    color = {"green": "#28a745", "yellow": "#f0ad4e", "red": "#dc3545"}.get(status, "#999")
    bg = {"green": "#e9f7ef", "yellow": "#fff8e1", "red": "#fce4e4"}.get(status, "#f5f5f5")

    if status == "unknown":
        headline = "🛠 Infrastructure: status unavailable (sweep may not have run yet)"
        body = ""
    elif count == 0:
        headline = f"🛠 Infrastructure: {icon} {status} — 0 findings, all clean"
        body = ""
    else:
        headline = f"🛠 Infrastructure: {icon} {status} — {count} finding{'s' if count != 1 else ''}"
        rows = "".join(f"<li style='margin:2px 0;'>{p}</li>" for p in preview[:5])
        body = f"<ul style='margin:6px 0 0 0;padding-left:24px;font-size:12px;color:#555;'>{rows}</ul>"

    return (
        f"<div style='background:{bg};border-left:4px solid {color};"
        f"padding:10px 14px;margin:14px 0;border-radius:4px;'>"
        f"<div style='font-size:14px;color:#333;'><b>{headline}</b></div>"
        f"{body}"
        f"</div>"
    )


def fetch_infrastructure_state() -> Dict[str, Any]:
    """Read infrastructure_state.md from avo-telemetry, parse status + findings.

    Returns a dict shaped:
      {status: "green"|"yellow"|"red"|"unknown",
       icon: "🟢"|"🟡"|"🔴"|"⚪",
       findings_count: int,
       findings_preview: [str, ...]  # top 3-5 finding titles, severity-ordered}

    Falls back to neutral 'unknown' state if the file can't be read.
    """
    out: Dict[str, Any] = {
        "status": "unknown",
        "icon": "⚪",
        "findings_count": 0,
        "findings_preview": [],
    }
    try:
        from services.cockpit_bridge import get_bridge_config, _get_file
        cfg = get_bridge_config()
        if not cfg:
            return out
        result = _get_file(cfg, "infrastructure_state.md")
        if not result:
            return out
        content, _ = result
    except Exception as e:
        logger.warning(f"[Briefing] infra state fetch failed: {e}")
        return out

    import re as _re
    # Parse Status — matches both "🟡 yellow" (post-sweep) and bare "yellow" (baseline)
    icon_for = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    for line in content.splitlines()[:30]:
        if "**Status:**" not in line and "Status:" not in line:
            continue
        m = _re.search(r"(🟢|🟡|🔴)?\s*(green|yellow|red)\b", line, _re.IGNORECASE)
        if m:
            status_word = m.group(2).lower()
            out["status"] = status_word
            out["icon"] = m.group(1) or icon_for.get(status_word, "⚪")
            break
    # Parse "## Active items" headline finding count, e.g. "· 7 findings"
    fc = _re.search(r"·\s*(\d+)\s+findings?", content)
    if fc:
        out["findings_count"] = int(fc.group(1))

    # Pull top finding titles from the bullet lines under ### Critical / ### Warn sections
    preview: list = []
    for sev_marker in ("🚨", "⚠️", "ℹ️"):
        # Each finding line: "- {icon} **[check]** Title\n  detail"
        for m in _re.finditer(rf"^- {sev_marker}\s+\*\*\[[^\]]+\]\*\*\s+(.+)$", content, _re.MULTILINE):
            title = m.group(1).strip()
            preview.append(f"{sev_marker} {title}")
            if len(preview) >= 5:
                break
        if len(preview) >= 5:
            break
    out["findings_preview"] = preview
    return out


def _revenue_section_html(rev: Dict[str, Any]) -> str:
    """Render the CRO revenue pipeline section."""
    if not rev.get("wired"):
        return (
            "<h2 style='margin-top:24px;'>🏆 Revenue (CRO)</h2>"
            "<div style='background:#f5f5f5;padding:14px;margin:10px 0;"
            "border-left:4px solid #999;border-radius:4px;color:#666;font-size:13px;'>"
            "Twenty pipeline integration pending. Once wired, this section shows: "
            "pipeline value, active deals by brand, stalled deals (>7d), replies "
            "pending (>4h), bookings + closes in last 24h."
            "</div>"
        )

    headline = (
        f"<b>Pipeline:</b> ${rev['pipeline_value']:,} · "
        f"{rev['deals_active']} active · "
        f"{rev['bookings_24h']} bookings 24h · "
        f"{rev['closed_24h']} closed 24h"
    )

    alerts = []
    if rev["replies_pending_4h"] > 0:
        alerts.append(
            f"<li style='color:#c00;'>{rev['replies_pending_4h']} replies pending > 4 hours, triage now</li>"
        )
    if rev["deals_stalled_7d"] > 0:
        alerts.append(
            f"<li style='color:#c00;'>{rev['deals_stalled_7d']} deals stalled > 7 days, re-engage or close out</li>"
        )
    alerts_html = (
        f"<ul style='margin:8px 0 0;padding-left:20px;font-size:13px;'>{''.join(alerts)}</ul>"
        if alerts else ""
    )

    by_brand = rev.get("by_brand", {})
    brand_rows = []
    for brand in ["WD", "AvI", "AIPG", "Book'd"]:
        b = by_brand.get(brand, {})
        if not b.get("wired"):
            brand_rows.append(
                f"<tr style='color:#999;'><td style='padding:6px 10px;'><b>{brand}</b></td>"
                f"<td style='padding:6px 10px;' colspan='4'><i>workspace not wired</i></td></tr>"
            )
            continue
        brand_rows.append(
            f"<tr><td style='padding:6px 10px;'><b>{brand}</b></td>"
            f"<td style='padding:6px 10px;'>${b.get('pipeline_value', 0):,}</td>"
            f"<td style='padding:6px 10px;'>{b.get('deals_active', 0)} active</td>"
            f"<td style='padding:6px 10px;'>{b.get('bookings_24h', 0)} bookings</td>"
            f"<td style='padding:6px 10px;'>{b.get('closed_24h', 0)} closed</td></tr>"
        )
    brand_table = (
        f"<table style='border-collapse:collapse;width:100%;font-size:13px;margin-top:10px;'>"
        f"<tr style='background:#f5f5f5;'>"
        f"<th style='padding:6px 10px;text-align:left;'>Brand</th>"
        f"<th style='padding:6px 10px;text-align:left;'>Pipeline</th>"
        f"<th style='padding:6px 10px;text-align:left;'>Active</th>"
        f"<th style='padding:6px 10px;text-align:left;'>24h Bookings</th>"
        f"<th style='padding:6px 10px;text-align:left;'>24h Closed</th></tr>"
        f"{''.join(brand_rows)}</table>"
    )

    return (
        f"<h2 style='margin-top:24px;'>🏆 Revenue (CRO)</h2>"
        f"<div style='background:#e9f7ef;padding:14px;border-left:4px solid #28a745;"
        f"border-radius:4px;font-size:14px;'>{headline}{alerts_html}</div>"
        f"{brand_table}"
    )


def compose_briefing_html(
    activity: Dict[str, Dict],
    tyler: Dict[str, Any],
    ryan: Dict[str, Any],
    revenue: Dict[str, Any] = None,
) -> str:
    """Compose a structured HTML briefing."""
    today = datetime.now().strftime("%A, %B %d %Y")
    infra = fetch_infrastructure_state()
    if revenue is None:
        revenue = pull_revenue_pipeline_summary()
    revenue_html = _revenue_section_html(revenue)

    # ── Header metrics ──
    tyler_total = tyler.get("total_leads", 0)
    tyler_sent = tyler.get("sent_24h", 0)
    tyler_replies = tyler.get("replies_24h", 0)
    tyler_boxes = len(tyler.get("box_box", []))

    ryan_total = ryan.get("total_leads", 0)
    ryan_sent = ryan.get("sent_24h", 0)
    ryan_replies = ryan.get("replies_24h", 0)
    ryan_boxes = len(ryan.get("box_box", []))

    total_sent_24h = tyler_sent + ryan_sent
    total_replies_24h = tyler_replies + ryan_replies

    # ── Deliverability alert (escalates above the fold when inbox placement is dead) ──
    deliverability_html = ""
    dead_campaigns = []
    if tyler.get("deliverability_dead"):
        dead_campaigns.append(("Tyler — AI Phone Guy", tyler))
    if ryan.get("deliverability_dead"):
        dead_campaigns.append(("Ryan Data — Automotive Intelligence", ryan))
    if dead_campaigns:
        rows = "".join(
            f"<tr><td style='padding:6px 10px;'><b>{name}</b></td>"
            f"<td style='padding:6px 10px;'>{d.get('lifetime_sent', 0)} sent</td>"
            f"<td style='padding:6px 10px;color:#c00;'>{d.get('lifetime_opens', 0)} opens "
            f"({d.get('open_rate_pct', 0)}%)</td>"
            f"<td style='padding:6px 10px;color:#c00;'>{d.get('lifetime_replies', 0)} replies</td></tr>"
            for name, d in dead_campaigns
        )
        deliverability_html = (
            f"<div style='background:#fce4e4;border:2px solid #c00;"
            f"padding:14px;margin:16px 0;border-radius:6px;'>"
            f"<h2 style='margin:0 0 8px;color:#900;'>🚨 Deliverability Alert — Inbox Placement Dead</h2>"
            f"<p style='margin:0 0 10px;color:#600;font-size:13px;'>"
            f"Volume is going out but engagement is zero. Likely causes: spam folder placement, "
            f"open-tracking pixel blocked, SPF/DKIM/DMARC misconfigured, or sender domain reputation in the gutter."
            f"</p>"
            f"<table style='border-collapse:collapse;width:100%;font-size:13px;'>{rows}</table>"
            f"</div>"
        )

    # ── BOX BOX alerts (prominently displayed) ──
    box_box_html = ""
    all_boxes = tyler.get("box_box", []) + ryan.get("box_box", [])
    if all_boxes:
        rows = "".join(
            f"<tr><td style='padding:6px 10px;'><b>{b['company']}</b></td>"
            f"<td style='padding:6px 10px;'>{b['email']}</td>"
            f"<td style='padding:6px 10px;color:#c00;'>{b['signal']}</td></tr>"
            for b in all_boxes
        )
        box_box_html = (
            f"<div style='background:#fff3cd;border:2px solid #ffc107;"
            f"padding:14px;margin:16px 0;border-radius:6px;'>"
            f"<h2 style='margin:0 0 10px;color:#856404;'>🏁 BOX BOX — Act Now</h2>"
            f"<table style='border-collapse:collapse;width:100%;font-size:13px;'>{rows}</table>"
            f"</div>"
        )

    # ── Agents by business ──
    agents_html = ""
    for business in ["AI Phone Guy", "Calling Digital", "Automotive Intelligence",
                     "Customer Advocate", "Agent Empire", "Executive"]:
        agents_in_biz = [a for a, (b, _) in AGENT_CATALOG.items() if b == business]
        if agents_in_biz:
            agents_html += _agent_section(business, agents_in_biz, activity)

    # ── Campaign summary table ──
    def _campaign_row(name: str, data: Dict) -> str:
        status_color = "#28a745" if data.get("status") == "Active" else "#dc3545"
        return (
            f"<tr><td style='padding:8px 12px;'><b>{name}</b></td>"
            f"<td style='padding:8px 12px;color:{status_color};'>{data.get('status','?')}</td>"
            f"<td style='padding:8px 12px;'>{data.get('total_leads',0)} leads</td>"
            f"<td style='padding:8px 12px;'>{data.get('sent_24h',0)} sent</td>"
            f"<td style='padding:8px 12px;'>{data.get('opens_24h',0)} opens</td>"
            f"<td style='padding:8px 12px;'>{data.get('replies_24h',0)} replies</td>"
            f"<td style='padding:8px 12px;'>{data.get('dnfs',0)} DNF</td></tr>"
        )

    campaign_html = (
        f"<h2 style='margin-top:24px;'>📧 Instantly Campaigns</h2>"
        f"<table style='border-collapse:collapse;width:100%;font-size:13px;"
        f"border:1px solid #ddd;'>"
        f"<tr style='background:#f5f5f5;'>"
        f"<th style='padding:8px 12px;text-align:left;'>Campaign</th>"
        f"<th style='padding:8px 12px;text-align:left;'>Status</th>"
        f"<th style='padding:8px 12px;text-align:left;'>Leads</th>"
        f"<th style='padding:8px 12px;text-align:left;'>Sent 24h</th>"
        f"<th style='padding:8px 12px;text-align:left;'>Opens</th>"
        f"<th style='padding:8px 12px;text-align:left;'>Replies</th>"
        f"<th style='padding:8px 12px;text-align:left;'>DNF</th></tr>"
        f"{_campaign_row('Tyler — AI Phone Guy', tyler)}"
        f"{_campaign_row('Ryan Data — Automotive Intelligence', ryan)}"
        f"</table>"
    )

    # ── Full HTML ──
    return f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;
color:#333;max-width:720px;margin:0 auto;padding:20px;">
<h1 style="margin:0 0 4px;">☀️ Morning Briefing</h1>
<div style="color:#888;margin-bottom:20px;">{today}</div>

<div style="background:#f8f9fa;padding:16px;border-radius:6px;margin-bottom:20px;">
  <b>Yesterday in 3 numbers:</b>
  <span style="font-size:18px;margin-left:12px;">
    {total_sent_24h} sent · {total_replies_24h} replies · {tyler_boxes + ryan_boxes} BOX BOX
  </span>
</div>

{deliverability_html}

{_cto_html(infra)}

{box_box_html}

{revenue_html}

{campaign_html}

<h2 style="margin-top:24px;">🤖 Agent Activity (last 24h)</h2>
<div style="color:#666;font-size:12px;margin-bottom:10px;">
Green ✓ = ran. Red ⚠ = silent (didn't run in last 24h — may need investigation).
</div>
{agents_html}

<hr style="margin:30px 0;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:12px;">
Generated automatically by Paperclip morning briefing. Runs every day at 8am CDT.<br>
Revenue section (CRO) added 2026-06-24. Twenty pipeline pull wires in next.<br>
To change recipient, timing, or content: edit rivers/shared/morning_briefing.py<br>
</div>
</body></html>"""


# ── Dispatchers ─────────────────────────────────────────────────────────────

def send_briefing_email(html_body: str, subject: str) -> bool:
    """Send the briefing via Resend."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("[Briefing] RESEND_API_KEY not set — skipping email")
        return False

    try:
        r = requests.post(
            RESEND_BASE,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": BRIEFING_FROM,
                "to": BRIEFING_RECIPIENTS,
                "subject": subject,
                "html": html_body,
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            logger.info(f"[Briefing] Email sent to {', '.join(BRIEFING_RECIPIENTS)}")
            return True
        logger.error(f"[Briefing] Resend error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"[Briefing] Email send failed: {e}")
        return False


def send_briefing_sms(headline: str) -> bool:
    """Optional: send the headline as an SMS via GHL."""
    if not BRIEFING_SMS_TO:
        return False
    ghl_key = os.getenv("GHL_API_KEY", "").strip()
    ghl_loc = os.getenv("GHL_LOCATION_ID", "").strip()
    if not ghl_key or not ghl_loc:
        return False

    try:
        r = requests.post(
            "https://services.leadconnectorhq.com/conversations/messages",
            headers={
                "Authorization": f"Bearer {ghl_key}",
                "Version": "2021-04-15",
                "Content-Type": "application/json",
            },
            json={
                "type": "SMS",
                "locationId": ghl_loc,
                "toNumber": BRIEFING_SMS_TO,
                "fromNumber": os.getenv("GHL_FROM_NUMBER", ""),
                "message": headline,
            },
            timeout=15,
        )
        if r.status_code in (200, 201):
            logger.info(f"[Briefing] SMS sent to {BRIEFING_SMS_TO}")
            return True
        logger.warning(f"[Briefing] SMS send failed {r.status_code}: {r.text[:150]}")
        return False
    except Exception as e:
        logger.error(f"[Briefing] SMS failed: {e}")
        return False


# ── Entry point (called by APScheduler) ─────────────────────────────────────

def morning_briefing_run() -> dict:
    """Pull all data, compose the briefing, send email + SMS."""
    logger.info("[Briefing] === Morning briefing start ===")

    # Pull agent activity
    activity = pull_agent_activity_last_24h()

    # Pull Instantly campaigns
    tyler = pull_instantly_campaign_summary(
        os.getenv("INSTANTLY_API_KEY_TYLER", "").strip(),
        os.getenv("INSTANTLY_CAMPAIGN_TYLER", "").strip(),
    )
    ryan = pull_instantly_campaign_summary(
        os.getenv("INSTANTLY_API_KEY", "").strip(),
        os.getenv("INSTANTLY_CAMPAIGN_RYAN_DATA", "").strip(),
    )

    # Pull CRO revenue layer (stub until Twenty workspaces wire in)
    revenue = pull_revenue_pipeline_summary()

    # Compose briefing
    html = compose_briefing_html(activity, tyler, ryan, revenue)

    # Compose subject
    total_sent = tyler.get("sent_24h", 0) + ryan.get("sent_24h", 0)
    total_replies = tyler.get("replies_24h", 0) + ryan.get("replies_24h", 0)
    total_boxes = len(tyler.get("box_box", [])) + len(ryan.get("box_box", []))
    rev_suffix = ""
    if revenue.get("wired"):
        rev_suffix = f", {revenue.get('bookings_24h', 0)} bookings, {revenue.get('closed_24h', 0)} closed"
    subject = f"☀️ Morning Briefing — {total_sent} sent, {total_replies} replies, {total_boxes} BOX BOX{rev_suffix}"

    # SMS headline
    sms_headline = (
        f"Morning briefing: {total_sent} emails sent yesterday. "
        f"{total_replies} replies. {total_boxes} BOX BOX. "
        f"Full report in your inbox."
    )

    email_ok = send_briefing_email(html, subject)
    sms_ok = send_briefing_sms(sms_headline)

    result = {
        "email_sent": email_ok,
        "sms_sent": sms_ok,
        "total_sent_24h": total_sent,
        "total_replies_24h": total_replies,
        "box_box_count": total_boxes,
        "agents_active_24h": len(activity),
        "agents_catalog_size": len(AGENT_CATALOG),
        "revenue_wired": revenue.get("wired", False),
        "revenue_bookings_24h": revenue.get("bookings_24h", 0),
        "revenue_closed_24h": revenue.get("closed_24h", 0),
    }
    logger.info(f"[Briefing] Complete: {result}")
    return result
