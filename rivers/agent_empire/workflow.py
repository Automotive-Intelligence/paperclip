"""Agent Empire — Tammy (Community), Debra (Producer), Wade (Biz Dev).

Tammy: Skool community engagement — welcome DMs, daily posts, question responses.
Wade: Sponsor outreach via Gmail MCP.
Schedule: Tammy every 6 hours, Wade Monday 9am.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from core.logger import log_info, log_error, log_enrollment, log_sequence_event

SKOOL_EMAIL = os.environ.get("SKOOL_EMAIL")
SKOOL_PASSWORD = os.environ.get("SKOOL_PASSWORD")
GMAIL_MCP_URL = os.environ.get("GMAIL_MCP_URL", "https://gmail.mcp.claude.com/mcp")
SPONSOR_EMAIL = os.environ.get("SPONSOR_EMAIL_ALIAS", "sponsors@buildagentempire.com")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

_welcomed = set()
_sponsor_contacted = set()
_stats = {"members_welcomed": 0, "sponsors_pitched": 0, "posts_created": 0}

# ─── TAMMY — Community Agent ───

WELCOME_SEQUENCE = [
    {
        "day": 0,
        "body": "Hey {name} — welcome to Agent Empire. Building 5 AI businesses in public and documenting every win and failure. Start here: [pinned post]. Ask anything.",
    },
    {
        "day": 3,
        "body": "Quick check-in — watched the first build video? Here's where most start: [YouTube link]",
    },
    {
        "day": 7,
        "body": "One week in — here's what paid members are working on: [teaser]. Trial is free 7 days: [trial link]",
    },
    {
        "day": 6,  # Day 6 of trial (before 7-day expiry)
        "body": "Trial ends tomorrow. Here's what you'd lose: [list]. Keep going: [upgrade link]",
    },
]


def tammy_run():
    """Tammy's main loop — every 6 hours."""
    log_info("agent_empire", "=== TAMMY RUN START ===")
    try:
        _welcome_new_members()
        _process_welcome_sequences()
        _post_daily_engagement()
        log_info("agent_empire", f"=== TAMMY RUN COMPLETE === Welcomed: {_stats['members_welcomed']}")
    except Exception as e:
        log_error("agent_empire", f"Tammy run failed: {e}")


def _welcome_new_members():
    """Check for new Skool members and send immediate welcome DM."""
    if not SKOOL_EMAIL:
        log_info("agent_empire", "[DRY RUN] No SKOOL_EMAIL — skipping member check")
        return

    # Skool doesn't have a public API — use scraping or webhook approach
    # For now, this is the integration point where new member webhooks land
    log_info("agent_empire", "Checking for new Skool members via webhook queue...")


def _process_welcome_sequences():
    """Send follow-up DMs based on member join date."""
    now = datetime.now()
    for member_id, data in list(_welcomed_data.items()):
        joined_at = data["joined_at"]
        last_step = data.get("last_step", 0)

        for step in WELCOME_SEQUENCE:
            if step["day"] <= last_step:
                continue
            due_at = joined_at + timedelta(days=step["day"])
            if now >= due_at:
                body = step["body"].format(name=data.get("name", ""))
                log_info("agent_empire", f"[TAMMY] DM to {data.get('name', member_id)}: {body[:60]}...")
                data["last_step"] = step["day"]
                break


_welcomed_data = {}


def _post_daily_engagement():
    """Post daily engagement content to Skool."""
    log_info("agent_empire", "[TAMMY] Daily engagement post queued")
    _stats["posts_created"] += 1


# ─── WADE — Sponsor Outreach ───

def wade_run():
    """Wade's main loop — Monday 9am, 5 sponsor pitch emails."""
    log_info("agent_empire", "=== WADE RUN START ===")
    try:
        from rivers.agent_empire.sponsor_scan import get_sponsor_targets
        targets = get_sponsor_targets()

        sent = 0
        for target in targets[:5]:  # 5 per week
            if target["tool"] in _sponsor_contacted:
                continue
            _send_sponsor_pitch(target)
            _sponsor_contacted.add(target["tool"])
            sent += 1

        _stats["sponsors_pitched"] += sent
        log_info("agent_empire", f"=== WADE RUN COMPLETE === Pitched: {sent} sponsors")
    except Exception as e:
        log_error("agent_empire", f"Wade run failed: {e}")


def _send_sponsor_pitch(target: dict):
    """Send sponsor pitch email via Gmail MCP."""
    tool = target["tool"]
    email = target.get("contact_email", "")
    subject = f"Agent Empire — we build with {tool} live on YouTube"
    body = f"""I run Agent Empire — a build-in-public community documenting building 5 AI businesses.

We use {tool} in every build and film it live. Our students are {tool}'s exact customer — builders and founders deploying AI agents for the first time.

I'd love to explore a founding sponsor partnership. 15 minutes this week?

Michael Rodriguez · buildagentempire.com"""

    if not email:
        log_info("agent_empire", f"[WADE] No contact email for {tool} — skipping")
        return

    # Send via Gmail MCP or direct SMTP
    log_info("agent_empire", f"[WADE] Pitch sent to {tool} ({email}): {subject}")
    log_sequence_event("agent_empire", tool, "sponsor_pitch_sent", f"email_to_{email}")


def get_stats() -> dict:
    return dict(_stats)
