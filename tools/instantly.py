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

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"


def _api_key() -> str:
    return (os.getenv("INSTANTLY_API_KEY") or "").strip()


def instantly_ready() -> bool:
    return bool(_api_key())


def _instantly_request(
    action: str,
    method: str,
    path: str,
    json_body: dict = None,
    params: dict = None,
    timeout: int = 15,
) -> dict:
    """Make an Instantly v2 API request with Bearer auth header."""
    key = _api_key()
    if not key:
        raise ValueError("INSTANTLY_API_KEY not configured")

    url = f"{INSTANTLY_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        if method.upper() == "GET":
            r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
        else:
            r = requests.post(url, headers=headers, json=json_body or {}, timeout=timeout)

        if r.status_code == 429:
            logger.warning("[Instantly] Rate limited on %s, retrying in 3s", action)
            time.sleep(3)
            if method.upper() == "GET":
                r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
            else:
                r = requests.post(url, headers=headers, json=json_body or {}, timeout=timeout)

        if r.status_code not in (200, 201):
            logger.error("[Instantly] %s failed: %d %s", action, r.status_code, r.text[:300])
            return {"_error": True, "status_code": r.status_code, "body": r.text[:300]}

        return r.json() if r.text.strip() else {}
    except Exception as e:
        logger.error("[Instantly] %s exception: %s", action, e)
        return {"_error": True, "exception": str(e)}


# ── Campaign Management ──────────────────────────────────────────────────────

def list_campaigns() -> list:
    """List all campaigns in the Instantly workspace (v2 API)."""
    result = _instantly_request("list_campaigns", "GET", "/campaigns", params={"limit": 100})
    if not isinstance(result, dict) or result.get("_error"):
        return []
    return result.get("items", []) or []


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
    """Add leads to an Instantly v2 campaign.

    v2 uses POST /api/v2/leads with one lead per request. The campaign
    UUID goes on the lead body as the `campaign` field. Custom merge-tag
    fields go inside `custom_variables`.
    """
    if not campaign_id:
        logger.error("[Instantly] No campaign_id provided")
        return {"status": "error", "reason": "no_campaign_id", "added": 0, "failed": 0}

    added = 0
    failed = 0
    failures = []

    for lead in leads:
        email = (lead.get("email") or "").strip()
        if not email:
            continue

        custom_vars = {}
        for key in (
            "phone", "website", "city", "business_type", "group_affiliation",
            "verified_fact", "trigger_event", "competitive_insight", "reason",
        ):
            val = lead.get(key, "")
            if val:
                custom_vars[key] = str(val)

        body = {
            "email": email,
            "first_name": lead.get("first_name", "") or None,
            "last_name": lead.get("last_name", "") or None,
            "company_name": lead.get("company_name", "") or None,
            "phone": lead.get("phone", "") or None,
            "website": lead.get("website", "") or None,
            "campaign": campaign_id,
            "skip_if_in_campaign": True,
            "skip_if_in_workspace": False,
        }
        if custom_vars:
            body["custom_variables"] = custom_vars

        body = {k: v for k, v in body.items() if v is not None}

        result = _instantly_request("create_lead", "POST", "/leads", json_body=body)

        if isinstance(result, dict) and not result.get("_error") and result.get("id"):
            added += 1
            logger.info("[Instantly] ✓ Added lead %s to campaign %s (lead_id=%s)", email, campaign_id, result.get("id"))
        else:
            failed += 1
            failures.append({"email": email, "error": result})
            logger.error("[Instantly] ✗ Failed to add lead %s to campaign %s: %s", email, campaign_id, result)

    status = "ok" if failed == 0 else ("partial" if added > 0 else "error")
    logger.info("[Instantly] add_leads_to_campaign %s: added=%d failed=%d (campaign=%s)", status, added, failed, campaign_id)
    return {"status": status, "added": added, "failed": failed, "failures": failures}


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
