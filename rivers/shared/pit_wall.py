"""Shared Pit Wall — F1 Race Analytics for all RevOps agents.

Used by Joshua (AI Phone Guy), Darrell (Automotive Intelligence),
and Brenda (Calling Digital) to monitor their respective email
campaigns and produce Race Reports with grid positions.

Each agent calls run_pit_wall() with their campaign config.
"""

import logging
import requests
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"


# ── Grid Position Logic ────────────────────────────────────────────────────

def classify_lead(lead: dict) -> tuple:
    """Returns (position_label, position_tag, priority).

    Priority: 1 = BOX BOX (act now), 2 = points finish, 3 = midfield,
              4 = backmarker, 5 = DNF.
    """
    replies = lead.get("email_reply_count", 0) or 0
    opens = lead.get("email_open_count", 0) or 0
    clicks = lead.get("email_click_count", 0) or 0
    status = lead.get("status", 0)

    # DNF — bounced or error status
    if status in (-1, -2, -3):
        return "DNF", "pit-dnf", 5

    # P1-P3 — BOX BOX: replied or clicked
    if replies > 0:
        return "P1 — BOX BOX (replied)", "pit-p1", 1
    if clicks > 0:
        return "P2 — BOX BOX (clicked booking)", "pit-p2", 1

    # P4-P10 — Points finish: multiple opens
    if opens >= 3:
        return "P4 — Hot (3+ opens)", "pit-p4", 2
    if opens == 2:
        return "P7 — Warm (2 opens)", "pit-p7", 2

    # P11-P15 — Midfield: opened once
    if opens == 1:
        return "P12 — Opened once", "pit-p12", 3

    # P16-P20 — Backmarker: no engagement
    return "P18 — No opens", "pit-p18", 4


# ── Instantly Lead Puller ───────────────────────────────────────────────────

def pull_instantly_leads(api_key: str, campaign_id: str) -> list:
    """Pull all leads from an Instantly campaign."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    leads = []
    cursor = None

    for _ in range(10):  # max 1000 leads
        body = {"campaign": campaign_id, "limit": 100}
        if cursor:
            body["starting_after"] = cursor
        try:
            r = requests.post(
                f"{INSTANTLY_BASE}/leads/list",
                headers=headers, json=body, timeout=15,
            )
            if r.status_code != 200:
                break
            data = r.json()
            leads.extend(data.get("items", []))
            cursor = data.get("next_starting_after")
            if not cursor:
                break
        except Exception as e:
            logger.error(f"[PitWall] Lead list page failed: {e}")
            break

    return leads


def pull_instantly_analytics(api_key: str, campaign_id: str) -> dict:
    """Pull campaign-level analytics from Instantly."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        r = requests.post(
            f"{INSTANTLY_BASE}/campaigns/analytics",
            headers=headers,
            json={"campaign_id": campaign_id},
            timeout=15,
        )
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        logger.error(f"[PitWall] Analytics pull failed: {e}")
        return {}


# ── Race Report Builder ────────────────────────────────────────────────────

def build_race_report(
    agent_name: str,
    business_name: str,
    leads: list,
    analytics: dict = None,
) -> dict:
    """Classify all leads and produce an F1-style Race Report.

    Returns dict with grid, box_box alerts, dnfs, metrics, and report text.
    """
    grid = []
    box_box = []
    dnfs = []

    for lead in leads:
        position, tag, priority = classify_lead(lead)
        entry = {
            "email": lead.get("email", ""),
            "company": lead.get("company_name", ""),
            "first_name": lead.get("first_name", ""),
            "position": position,
            "tag": tag,
            "priority": priority,
            "opens": lead.get("email_open_count", 0) or 0,
            "clicks": lead.get("email_click_count", 0) or 0,
            "replies": lead.get("email_reply_count", 0) or 0,
        }
        grid.append(entry)
        if priority == 1:
            box_box.append(entry)
        elif priority == 5:
            dnfs.append(entry)

    grid.sort(key=lambda x: (x["priority"], -x["opens"]))

    total = len(grid)
    total_opens = sum(e["opens"] for e in grid)
    total_clicks = sum(e["clicks"] for e in grid)
    total_replies = sum(e["replies"] for e in grid)

    open_rate = (total_opens / total * 100) if total else 0
    click_rate = (total_clicks / total * 100) if total else 0
    reply_rate = (total_replies / total * 100) if total else 0

    if open_rate >= 40:
        flag = "GREEN FLAG"
    elif open_rate >= 20:
        flag = "YELLOW FLAG"
    else:
        flag = "RED FLAG"

    # Build report text
    lines = [
        f"=== RACE REPORT — {agent_name.upper()} / {business_name} ===",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M CST')}",
        f"Flag: {flag}",
        f"Leads in race: {total}",
        f"Open rate: {open_rate:.1f}% | Click rate: {click_rate:.1f}% | Reply rate: {reply_rate:.1f}%",
        "",
    ]

    if box_box:
        lines.append("BOX BOX — ACT NOW:")
        for b in box_box:
            lines.append(f"  {b['position']}: {b['company']} ({b['email']})")
        lines.append("")

    lines.append("FULL GRID:")
    for entry in grid:
        lines.append(
            f"  {entry['position']}: {entry['company']} ({entry['email']}) "
            f"— opens={entry['opens']} clicks={entry['clicks']} replies={entry['replies']}"
        )

    if dnfs:
        lines.append("")
        lines.append("DNF (remove from pipeline):")
        for d in dnfs:
            lines.append(f"  {d['company']} ({d['email']})")

    report = "\n".join(lines)

    return {
        "grid": grid,
        "box_box": box_box,
        "dnfs": dnfs,
        "total": total,
        "open_rate": round(open_rate, 1),
        "click_rate": round(click_rate, 1),
        "reply_rate": round(reply_rate, 1),
        "flag": flag,
        "report": report,
    }
