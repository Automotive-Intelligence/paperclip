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

BRIEFING_RECIPIENT = os.getenv("BRIEFING_RECIPIENT", "michael@automotiveintelligence.io")
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
    "coo_agent": ("Executive", "COO"),
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

    # Campaign analytics for 24h metrics
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
        r = requests.post(f"{INSTANTLY_BASE}/campaigns/analytics",
            headers=headers,
            json={"campaign_id": campaign_id, "start_date": since},
            timeout=20)
        if r.status_code == 200:
            stats = r.json()
            if isinstance(stats, list) and stats:
                stats = stats[0]
            summary["sent_24h"] = stats.get("emails_sent_count", 0) or 0
            summary["opens_24h"] = stats.get("open_count_unique", stats.get("open_count", 0)) or 0
            summary["replies_24h"] = stats.get("reply_count", 0) or 0
            summary["clicks_24h"] = stats.get("click_count", 0) or 0
    except Exception as e:
        logger.warning(f"[Briefing] analytics pull failed: {e}")

    return summary


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


def compose_briefing_html(
    activity: Dict[str, Dict],
    tyler: Dict[str, Any],
    ryan: Dict[str, Any],
) -> str:
    """Compose a structured HTML briefing."""
    today = datetime.now().strftime("%A, %B %d %Y")

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

{box_box_html}

{campaign_html}

<h2 style="margin-top:24px;">🤖 Agent Activity (last 24h)</h2>
<div style="color:#666;font-size:12px;margin-bottom:10px;">
Green ✓ = ran. Red ⚠ = silent (didn't run in last 24h — may need investigation).
</div>
{agents_html}

<hr style="margin:30px 0;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:12px;">
Generated automatically by Paperclip morning briefing. Runs every day at 8am CDT.<br>
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
                "to": [BRIEFING_RECIPIENT],
                "subject": subject,
                "html": html_body,
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            logger.info(f"[Briefing] Email sent to {BRIEFING_RECIPIENT}")
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

    # Compose briefing
    html = compose_briefing_html(activity, tyler, ryan)

    # Compose subject
    total_sent = tyler.get("sent_24h", 0) + ryan.get("sent_24h", 0)
    total_replies = tyler.get("replies_24h", 0) + ryan.get("replies_24h", 0)
    total_boxes = len(tyler.get("box_box", [])) + len(ryan.get("box_box", []))
    subject = f"☀️ Morning Briefing — {total_sent} sent, {total_replies} replies, {total_boxes} BOX BOX"

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
    }
    logger.info(f"[Briefing] Complete: {result}")
    return result
