"""
tools/email_engine.py — Universal AI Prospect Parser & Email Engine
Parses raw CrewAI output from ANY sales agent into structured prospect dicts
with ready-to-send cold emails. Powers Tyler, Marcus, and Ryan Data.
"""

import os
import re
import logging
import json
import requests


# ── Agent-specific parsing prompts ───────────────────────────────────────────

PARSE_PROMPTS = {
    "tyler": {
        "keys": """Each object must have exactly these keys:
- business_name (string)
- city (string)
- business_type (string, e.g. "HVAC", "Plumbing", "Dental", "Roofing", "Law Firm")
- reason (string, why they are being targeted)
- email_hook (string, the personalized cold email opening line)
- subject (string, the cold email subject line — 2-4 words, lowercase)
- body (string, the full cold email body text)
- follow_up_subject (string, the follow-up email subject line)
- follow_up_body (string, the follow-up email body text)
- email (string, business email if found, otherwise empty string)""",
        "context": "This is from a sales agent targeting local service businesses (HVAC, plumbing, etc.) in DFW Texas with cold emails.",
    },
    "marcus": {
        "keys": """Each object must have exactly these keys:
- business_name (string)
- city (string, default "Dallas" if not specified)
- business_type (string, e.g. "Restaurant", "Retail", "Professional Services")
- reason (string, the key pain point or opportunity identified)
- email_hook (string, the consultative opening line)
- subject (string, a consultative cold email subject line)
- body (string, the full cold email body — educational, not salesy, leads with their problem)
- follow_up_subject (string, follow-up email subject)
- follow_up_body (string, follow-up email body with different value angle)
- email (string, business email if found, otherwise empty string)
- bundle_candidate (boolean, true if flagged as AI Phone Guy bundle opportunity)""",
        "context": "This is from a consultative sales agent targeting Dallas businesses that need digital marketing and AI implementation services.",
    },
    "ryan_data": {
        "keys": """Each object must have exactly these keys:
- business_name (string, the dealership name)
- city (string, city in DFW area)
- business_type (string, always "Auto Dealership" or more specific like "Ford Dealership")
- reason (string, the AI readiness signal found)
- email_hook (string, the personalized assessment offer hook)
- subject (string, cold email subject line for dealer outreach)
- body (string, full cold email body positioning the free AI Readiness Assessment)
- follow_up_subject (string, follow-up subject line)
- follow_up_body (string, follow-up body with different angle)
- email (string, dealership contact email if found, otherwise empty string)
- group_affiliation (string, dealership group if known, otherwise empty string)""",
        "context": "This is from a CRO targeting DFW car dealerships with a free AI Readiness Assessment → $2,500 Audit → $7,500 Implementation pipeline.",
    },
}


def parse_prospects(raw_output: str, agent_name: str = "tyler") -> list:
    """
    Universal prospect parser. Takes any sales agent's raw CrewAI output
    and returns structured prospect dicts with ready-to-send emails.

    Uses Claude Haiku to reliably extract structured data from free-form text.
    """
    if not raw_output or len(raw_output.strip()) < 50:
        logging.warning(f"[Parser] {agent_name} output too short to parse.")
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logging.error("[Parser] ANTHROPIC_API_KEY not set — cannot parse prospects.")
        return []

    agent_config = PARSE_PROMPTS.get(agent_name, PARSE_PROMPTS["tyler"])

    prompt = f"""Extract the prospect list from this sales prospecting report.
Return ONLY a JSON array. No markdown, no explanation, just the raw JSON array.

Context: {agent_config['context']}

{agent_config['keys']}

If a cold email subject and body are present in the report, extract them exactly.
If they are NOT present, generate appropriate ones based on the prospect details and the agent's style.

For Tyler: Subject lines should be 2-4 words, lowercase, internal-looking (e.g. 'missed calls', 'after-hours voicemail').
Body should use Observation > Problem > Proof > Ask framework. CTA should be interest-based ('Worth a quick look?').

For Marcus: Subject should be consultative (e.g. 'quick audit for [business]').
Body should lead with their problem and what it's costing them. Educational, not salesy.

For Ryan Data: Subject should reference automotive/dealership context.
Body should position the free AI Readiness Assessment as the entry point.

For ALL agents: Include CAN-SPAM compliant unsubscribe language at the end of body:
"If you'd rather not hear from us, just reply 'stop' and we'll remove you immediately."

If any field is missing, use an empty string.

REPORT:
{raw_output[:4000]}

JSON array:"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 3000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()

        # Strip any accidental markdown fences
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        prospects = json.loads(content)
        logging.info(f"[Parser] Extracted {len(prospects)} prospects from {agent_name}'s output.")
        return prospects

    except json.JSONDecodeError as e:
        logging.error(f"[Parser] JSON parse failed for {agent_name}: {e}")
        return []
    except Exception as e:
        logging.error(f"[Parser] Prospect parsing failed for {agent_name}: {e}")
        return []


def parse_retention_actions(raw_output: str, agent_name: str = "jennifer") -> list:
    """
    Parse retention agent output into actionable items:
    - Emails to send to at-risk clients
    - Upsell messages to send to expansion-ready clients
    - Check-in templates ready to fire

    Returns list of dicts with: client_type, action, subject, body, urgency
    """
    if not raw_output or len(raw_output.strip()) < 50:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    prompt = f"""Extract actionable retention items from this client success report.
Return ONLY a JSON array. No markdown, no explanation.

Each object must have:
- action_type (string: "retention_email", "upsell_outreach", "check_in", "save_sequence")
- target_description (string: who this is for, e.g. "clients on Starter plan showing low usage")
- subject (string: email subject line)
- body (string: email body text, ready to send)
- urgency (string: "high", "medium", "low")
- trigger (string: what signal prompted this action)

Extract templates and talking points. If the report mentions email templates, extract them.
If it only has talking points, convert them into sendable email templates.

REPORT:
{raw_output[:3000]}

JSON array:"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        actions = json.loads(content)
        logging.info(f"[Parser] Extracted {len(actions)} retention actions from {agent_name}.")
        return actions
    except Exception as e:
        logging.error(f"[Parser] Retention parsing failed for {agent_name}: {e}")
        return []


def parse_content_pieces(raw_output: str, agent_name: str = "zoe") -> list:
    """
    Parse content agent output into publishable content pieces.
    Returns list of dicts with: platform, content_type, title, body, hashtags, cta, status

    This closes the loop: content agents generate → this parses → ready to publish.
    """
    if not raw_output or len(raw_output.strip()) < 50:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    prompt = f"""Extract publishable content pieces from this content marketing report.
Return ONLY a JSON array. No markdown, no explanation.

Each object must have:
- platform (string: "linkedin", "twitter", "instagram", "blog", "email", "newsletter")
- content_type (string: "post", "article", "story", "thread", "newsletter_section")
- title (string: headline or hook)
- body (string: full content ready to publish — not a summary, the actual post/article text)
- hashtags (string: relevant hashtags if social, empty string if not)
- cta (string: call to action)
- funnel_stage (string: "awareness", "consideration", "conversion")

If the report has a social post ready to publish, extract it exactly.
If it only has content plans/ideas, flesh them out into publishable drafts.

REPORT:
{raw_output[:4000]}

JSON array:"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 3000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        pieces = json.loads(content)
        logging.info(f"[Parser] Extracted {len(pieces)} content pieces from {agent_name}.")
        return pieces
    except Exception as e:
        logging.error(f"[Parser] Content parsing failed for {agent_name}: {e}")
        return []
