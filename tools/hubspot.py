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
from tools.outbound_email import email_delivery_mode, send_unified_email
from tools.email_templates import compose_templated_email, strict_template_validation_enabled
from tools.revenue_tracker import already_emailed

HUBSPOT_BASE_URL = "https://api.hubapi.com"


def _hubspot_token() -> str:
    return (os.getenv("HUBSPOT_API_KEY") or os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()


def hubspot_ready() -> bool:
    return bool(_hubspot_token())


def hubspot_email_ready() -> bool:
    """HubSpot first-touch email is enabled only when a transactional template is configured."""
    return hubspot_ready() and bool((os.getenv("HUBSPOT_TRANSACTIONAL_EMAIL_ID") or "").strip())


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


def _associate_deal(deal_id: str, object_type: str, object_id: str, assoc_type_id: int) -> None:
    """Associate a deal with a contact (type 3) or company (type 5). Fails silently — association
    is best-effort and should not block the prospect push."""
    if not deal_id or not object_id:
        return
    try:
        _hubspot_request(
            f"associate_deal_{object_type}",
            "PUT",
            f"/crm/v3/objects/deals/{deal_id}/associations/{object_type}/{object_id}/{assoc_type_id}",
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"[HubSpot] Could not associate deal {deal_id} with {object_type} {object_id}: {e}")


def _create_deal(prospect: dict, source_agent: str, contact_id: str = "", company_id: str = "") -> str:
    """Create a HubSpot deal and associate it with the given contact or company.

    Pipeline and deal stage are read from env vars so each customer can configure
    their own HubSpot pipeline without code changes:
        HUBSPOT_PIPELINE_ID   — defaults to 'default'
        HUBSPOT_DEAL_STAGE_NEW — defaults to 'appointmentscheduled'
    """
    pipeline_id = (os.getenv("HUBSPOT_PIPELINE_ID") or "default").strip()
    deal_stage = (os.getenv("HUBSPOT_DEAL_STAGE_NEW") or "appointmentscheduled").strip()
    props = {
        "dealname": f"{prospect.get('business_name', 'Unknown')} — {source_agent}",
        "pipeline": pipeline_id,
        "dealstage": deal_stage,
        "description": prospect.get("reason", ""),
    }
    try:
        data = _hubspot_request("create_deal", "POST", "/crm/v3/objects/deals", json_body={"properties": props}, timeout=15)
        deal_id = data.get("id", "")
        if deal_id:
            _associate_deal(deal_id, "contacts", contact_id, 3)   # deal → contact
            _associate_deal(deal_id, "companies", company_id, 5)  # deal → company
        return deal_id
    except Exception as e:
        logging.warning(f"[HubSpot] Deal creation failed: {e}")
        return ""


def _send_transactional_email(prospect: dict) -> bool:
    """Send first-touch email through HubSpot transactional email API.

    Requirements:
      - HUBSPOT_TRANSACTIONAL_EMAIL_ID env var must be set.
      - Prospect must contain email, subject, and body.

    The transactional template should support custom properties:
      - subject_line
      - body_copy
      - business_name
    """
    email = (prospect.get("email") or "").strip()
    subject = (prospect.get("subject") or "").strip()
    body = (prospect.get("body") or "").strip()
    email_id = (os.getenv("HUBSPOT_TRANSACTIONAL_EMAIL_ID") or "").strip()

    if not email or not subject or not body or not email_id:
        return False

    payload = {
        "emailId": int(email_id),
        "message": {
            "to": email,
            "customProperties": {
                "subject_line": subject,
                "body_copy": body,
                "business_name": prospect.get("business_name", ""),
            },
        },
    }

    try:
        _hubspot_request(
            "send_transactional_email",
            "POST",
            "/marketing/v3/transactional/single-email/send",
            json_body=payload,
            timeout=20,
        )
        return True
    except Exception as e:
        logging.warning("[HubSpot] Transactional email send failed for %s: %s", email, e)
        return False


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
                        "email_attempted": False,
                        "email_sent": False,
                        "template_key": "",
                        "template_valid": True,
                        "template_issues": [],
                        "provider": "hubspot",
                    })
                    continue
                new_id = _create_contact(p, source_agent, business_key)
                _create_deal(p, source_agent, contact_id=new_id)
                rendered = compose_templated_email(p, business_key=business_key, agent_name=source_agent)
                mode = email_delivery_mode()
                if strict_template_validation_enabled() and not rendered.get("valid", False):
                    email_attempted = False
                    email_sent = False
                    logging.warning(
                        "[HubSpot] Email blocked by template validation for %s: %s",
                        business_name,
                        ",".join(rendered.get("issues", [])),
                    )
                elif email and already_emailed(email):
                    email_attempted = False
                    email_sent = False
                    logging.info(
                        "[HubSpot] Email skipped for %s — already emailed %s within 90 days.",
                        business_name, email,
                    )
                elif mode == "unified":
                    email_attempted = bool((p.get("email") or "").strip() and rendered.get("subject") and rendered.get("body_text"))
                    email_sent = send_unified_email(
                        p.get("email", ""),
                        rendered.get("subject", ""),
                        rendered.get("body_text", ""),
                        business_key=business_key,
                    ) if email_attempted else False
                else:
                    if rendered.get("subject"):
                        p["subject"] = rendered.get("subject", "")
                    if rendered.get("body_text"):
                        p["body"] = rendered.get("body_text", "")
                    email_attempted = bool((p.get("email") or "").strip() and rendered.get("subject") and rendered.get("body_text") and hubspot_email_ready())
                    email_sent = _send_transactional_email(p) if email_attempted else False
                results.append({
                    "business_name": business_name,
                    "contact_id": new_id,
                    "status": "created",
                    "contact_email": email,
                    "email_attempted": email_attempted,
                    "email_sent": email_sent,
                    "template_key": rendered.get("template_key", ""),
                    "template_valid": bool(rendered.get("valid", False)),
                    "template_issues": rendered.get("issues", []),
                    "deal_created": True,
                    "provider": "hubspot",
                })
                continue

            existing_company_id = _search_company_by_name(business_name)
            if existing_company_id:
                results.append({
                    "business_name": business_name,
                    "contact_id": existing_company_id,
                    "status": "duplicate_skipped",
                    "email_attempted": False,
                    "email_sent": False,
                    "template_key": "",
                    "template_valid": True,
                    "template_issues": [],
                    "provider": "hubspot",
                })
                continue

            new_id = _create_company(p, source_agent, business_key)
            _create_deal(p, source_agent, company_id=new_id)
            results.append({
                "business_name": business_name,
                "contact_id": new_id,
                "status": "created",
                "email_attempted": False,
                "email_sent": False,
                "template_key": "",
                "template_valid": True,
                "template_issues": [],
                "deal_created": True,
                "provider": "hubspot",
            })
        except Exception as e:
            logging.error(f"[HubSpot] Failed to push prospect {business_name}: {e}")
            results.append({
                "business_name": business_name,
                "status": "failed",
                "email_attempted": False,
                "email_sent": False,
                "template_key": "",
                "template_valid": False,
                "template_issues": ["send_path_failed"],
                "provider": "hubspot",
                "error": str(e),
            })

    return results
