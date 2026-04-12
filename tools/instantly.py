"""
tools/instantly.py — Instantly.ai integration for Automotive Intelligence cold email campaigns.

Ryan Data prospects go to HubSpot (CRM data) + Instantly (email campaigns).
This module handles adding leads to Instantly campaigns with enrichment data
as custom variables for personalized email sequences.
"""

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

INSTANTLY_BASE = "https://api.instantly.ai/api/v1"


def _api_key() -> str:
    return (os.getenv("INSTANTLY_API_KEY") or "").strip()


def instantly_ready() -> bool:
    return bool(_api_key())


def _instantly_request(action: str, method: str, path: str, json_body: dict = None, timeout: int = 15) -> dict:
    """Make an Instantly API request with the api_key injected."""
    key = _api_key()
    if not key:
        raise ValueError("INSTANTLY_API_KEY not configured")

    url = f"{INSTANTLY_BASE}{path}"

    # v1 API uses api_key in the JSON body
    if json_body is None:
        json_body = {}
    json_body["api_key"] = key

    try:
        if method.upper() == "GET":
            r = requests.get(url, params={"api_key": key}, timeout=timeout)
        else:
            r = requests.post(url, json=json_body, timeout=timeout)

        if r.status_code == 429:
            logger.warning("[Instantly] Rate limited on %s, retrying in 3s", action)
            time.sleep(3)
            if method.upper() == "GET":
                r = requests.get(url, params={"api_key": key}, timeout=timeout)
            else:
                r = requests.post(url, json=json_body, timeout=timeout)

        if r.status_code not in (200, 201):
            logger.error("[Instantly] %s failed: %d %s", action, r.status_code, r.text[:300])
            return {}

        return r.json() if r.text.strip() else {}
    except Exception as e:
        logger.error("[Instantly] %s exception: %s", action, e)
        return {}


# ── Campaign Management ──────────────────────────────────────────────────────

def list_campaigns() -> list:
    """List all campaigns in the Instantly workspace."""
    return _instantly_request("list_campaigns", "GET", "/campaign/list") or []


def get_campaign_id_by_name(name: str) -> Optional[str]:
    """Find a campaign by name, return its ID or None."""
    campaigns = list_campaigns()
    if isinstance(campaigns, list):
        for c in campaigns:
            if isinstance(c, dict) and c.get("name", "").lower() == name.lower():
                return c.get("id")
    return None


# ── Lead Management ──────────────────────────────────────────────────────────

def add_leads_to_campaign(campaign_id: str, leads: list) -> dict:
    """Add leads to an Instantly campaign.

    Each lead dict should have:
        - email (required)
        - first_name, last_name, company_name (standard fields)
        - Any additional keys become custom_variables for merge tags
    """
    if not campaign_id:
        logger.error("[Instantly] No campaign_id provided")
        return {"status": "error", "reason": "no_campaign_id"}

    formatted_leads = []
    for lead in leads:
        email = (lead.get("email") or "").strip()
        if not email:
            continue

        # Standard Instantly fields
        instantly_lead = {
            "email": email,
            "first_name": lead.get("first_name", ""),
            "last_name": lead.get("last_name", ""),
            "company_name": lead.get("company_name", ""),
        }

        # Everything else goes into custom_variables for merge tags
        custom_vars = {}
        for key in ("phone", "website", "city", "business_type", "group_affiliation",
                     "verified_fact", "trigger_event", "competitive_insight", "reason"):
            val = lead.get(key, "")
            if val:
                custom_vars[key] = str(val)

        if custom_vars:
            instantly_lead["custom_variables"] = custom_vars

        formatted_leads.append(instantly_lead)

    if not formatted_leads:
        return {"status": "ok", "added": 0, "reason": "no_valid_leads"}

    result = _instantly_request("add_leads", "POST", "/lead/add", json_body={
        "campaign_id": campaign_id,
        "skip_if_in_workspace": False,
        "skip_if_in_campaign": True,
        "leads": formatted_leads,
    })

    added = len(formatted_leads)
    logger.info("[Instantly] Added %d leads to campaign %s", added, campaign_id)
    return {"status": "ok", "added": added, "response": result}


def add_prospect_to_instantly(prospect: dict, campaign_id: str) -> dict:
    """Add a single Ryan Data prospect to an Instantly campaign.

    Translates the prospect dict from Ryan Data's output format
    into Instantly's lead format with custom variables.
    """
    contact_name = (prospect.get("contact_name") or "").strip()
    parts = contact_name.split() if contact_name else []
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    lead = {
        "email": prospect.get("email", ""),
        "first_name": first_name,
        "last_name": last_name,
        "company_name": prospect.get("business_name", ""),
        "phone": prospect.get("phone", ""),
        "website": prospect.get("website", ""),
        "city": prospect.get("city", ""),
        "business_type": prospect.get("business_type", ""),
        "group_affiliation": prospect.get("group_affiliation", ""),
        "verified_fact": prospect.get("verified_fact", ""),
        "trigger_event": prospect.get("trigger_event", ""),
        "competitive_insight": prospect.get("competitive_insight", ""),
        "reason": prospect.get("reason", ""),
    }

    return add_leads_to_campaign(campaign_id, [lead])
