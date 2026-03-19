"""
tools/hubspot.py - HubSpot CRM connector for Paperclip sales pipeline.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import os
import logging
from typing import Optional

from services.errors import ServiceCallError
from services.http_client import request_with_retry

HUBSPOT_BASE_URL = "https://api.hubapi.com"


def _hubspot_token() -> str:
    return (os.getenv("HUBSPOT_API_KEY") or os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()


def hubspot_ready() -> bool:
    return bool(_hubspot_token())


def _headers() -> dict:
    token = _hubspot_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _hubspot_request(operation: str, method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None, timeout: int = 15) -> dict:
    response = request_with_retry(
        provider="hubspot",
        operation=operation,
        method=method,
        url=f"{HUBSPOT_BASE_URL}{path}",
        headers=_headers(),
        params=params,
        json_body=json_body,
        timeout=timeout,
        max_attempts=3,
        backoff_seconds=0.7,
    )
    if response.ok:
        return response.data or {}
    if response.error is None:
        raise RuntimeError(f"hubspot.{operation} failed with unknown error")
    raise ServiceCallError(response.error)


def _search_contact_by_email(email: str) -> Optional[str]:
    if not email:
        return None
    body = {
        "filterGroups": [
            {
                "filters": [
                    {"propertyName": "email", "operator": "EQ", "value": email}
                ]
            }
        ],
        "properties": ["email"],
        "limit": 1,
    }
    data = _hubspot_request("search_contact", "POST", "/crm/v3/objects/contacts/search", json_body=body, timeout=15)
    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("id")


def _search_company_by_name(name: str) -> Optional[str]:
    if not name:
        return None
    body = {
        "filterGroups": [
            {
                "filters": [
                    {"propertyName": "name", "operator": "EQ", "value": name}
                ]
            }
        ],
        "properties": ["name"],
        "limit": 1,
    }
    data = _hubspot_request("search_company", "POST", "/crm/v3/objects/companies/search", json_body=body, timeout=15)
    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("id")


def _create_contact(prospect: dict, source_agent: str, business_key: str) -> str:
    props = {
        "email": prospect.get("email", ""),
        "lastname": prospect.get("business_name") or "Unknown",
        "city": prospect.get("city", ""),
        "lifecyclestage": "lead",
        "hs_lead_status": "NEW",
        "company": prospect.get("business_name", ""),
        "website": "",
        "jobtitle": prospect.get("business_type", ""),
    }
    data = _hubspot_request("create_contact", "POST", "/crm/v3/objects/contacts", json_body={"properties": props}, timeout=15)
    return data.get("id", "")


def _create_company(prospect: dict, source_agent: str, business_key: str) -> str:
    props = {
        "name": prospect.get("business_name", "Unknown"),
        "city": prospect.get("city", ""),
        "description": f"{source_agent} prospecting: {prospect.get('reason', '')}",
    }
    data = _hubspot_request("create_company", "POST", "/crm/v3/objects/companies", json_body={"properties": props}, timeout=15)
    return data.get("id", "")


def push_prospects_to_hubspot(prospects: list, source_agent: str = "tyler", business_key: str = "autointelligence") -> list:
    """Push parsed prospects to HubSpot as contacts (preferred) or companies."""
    if not hubspot_ready():
        raise ValueError("HubSpot credentials not configured. Set HUBSPOT_API_KEY or HUBSPOT_ACCESS_TOKEN.")

    results = []
    for p in prospects:
        business_name = p.get("business_name", "")
        try:
            email = (p.get("email") or "").strip()
            if email:
                existing_contact_id = _search_contact_by_email(email)
                if existing_contact_id:
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_contact_id,
                        "status": "duplicate_skipped",
                        "provider": "hubspot",
                    })
                    continue
                new_id = _create_contact(p, source_agent, business_key)
                results.append({
                    "business_name": business_name,
                    "contact_id": new_id,
                    "status": "created",
                    "email_sent": False,
                    "provider": "hubspot",
                })
                continue

            existing_company_id = _search_company_by_name(business_name)
            if existing_company_id:
                results.append({
                    "business_name": business_name,
                    "contact_id": existing_company_id,
                    "status": "duplicate_skipped",
                    "provider": "hubspot",
                })
                continue

            new_id = _create_company(p, source_agent, business_key)
            results.append({
                "business_name": business_name,
                "contact_id": new_id,
                "status": "created",
                "email_sent": False,
                "provider": "hubspot",
            })
        except Exception as e:
            logging.error(f"[HubSpot] Failed to push prospect {business_name}: {e}")
            results.append({
                "business_name": business_name,
                "status": "failed",
                "provider": "hubspot",
                "error": str(e),
            })

    return results
