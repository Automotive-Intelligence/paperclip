"""
tools/attio.py - Attio CRM connector for Paperclip sales pipeline.
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

from services.errors import ServiceCallError
from services.http_client import request_with_retry

ATTIO_BASE_URL = "https://api.attio.com/v2"


def _attio_token() -> str:
    return (os.getenv("ATTIO_API_KEY") or "").strip()


def attio_ready() -> bool:
    return bool(_attio_token())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_attio_token()}",
        "Content-Type": "application/json",
    }


def _attio_request(operation: str, method: str, path: str, *, json_body: dict | None = None, timeout: int = 15) -> dict:
    response = request_with_retry(
        provider="attio",
        operation=operation,
        method=method,
        url=f"{ATTIO_BASE_URL}{path}",
        headers=_headers(),
        json_body=json_body,
        timeout=timeout,
        max_attempts=3,
        backoff_seconds=0.7,
    )
    if response.ok:
        return response.data or {}
    if response.error is None:
        raise RuntimeError(f"attio.{operation} failed with unknown error")
    raise ServiceCallError(response.error)


def _search_company_by_name(name: str) -> str | None:
    """Return existing record_id if a company with this name exists, else None."""
    if not name:
        return None
    payload = {
        "filter": {"name": {"$equals": name}},
        "limit": 1,
    }
    try:
        data = _attio_request("search_company", "POST", "/objects/companies/records/query", json_body=payload, timeout=15)
        records = data.get("data", [])
        if not records:
            return None
        rec = records[0]
        return rec.get("id", {}).get("record_id") if isinstance(rec.get("id"), dict) else None
    except Exception:
        return None  # fail open — attempt create rather than silently skip


def _search_person_by_email(email: str) -> str | None:
    """Return existing record_id if a person with this email exists, else None."""
    if not email:
        return None
    payload = {
        "filter": {"email_addresses": {"$contains": email}},
        "limit": 1,
    }
    try:
        data = _attio_request("search_person", "POST", "/objects/people/records/query", json_body=payload, timeout=15)
        records = data.get("data", [])
        if not records:
            return None
        rec = records[0]
        return rec.get("id", {}).get("record_id") if isinstance(rec.get("id"), dict) else None
    except Exception:
        return None  # fail open


def _create_company_record(prospect: dict, source_agent: str, business_key: str) -> str:
    # Attio values are arrays of value objects for many system fields.
    payload = {
        "data": {
            "values": {
                "name": [{"value": prospect.get("business_name", "Unknown")}],
                "description": [{"value": f"{source_agent} prospecting: {prospect.get('reason', '')}"}],
            }
        }
    }
    data = _attio_request("create_company_record", "POST", "/objects/companies/records", json_body=payload, timeout=15)
    record = data.get("data", {})
    return record.get("id", {}).get("record_id", "") if isinstance(record.get("id"), dict) else record.get("id", "")


def _create_person_record(prospect: dict, source_agent: str, business_key: str) -> str:
    email = (prospect.get("email") or "").strip()
    name = (prospect.get("business_name") or "Unknown").strip()
    payload = {
        "data": {
            "values": {
                "name": [{"value": name}],
                "email_addresses": [{"value": email}] if email else [],
                "description": [{"value": f"{source_agent} prospecting: {prospect.get('reason', '')}"}],
            }
        }
    }
    data = _attio_request("create_person_record", "POST", "/objects/people/records", json_body=payload, timeout=15)
    record = data.get("data", {})
    return record.get("id", {}).get("record_id", "") if isinstance(record.get("id"), dict) else record.get("id", "")


def push_prospects_to_attio(prospects: list, source_agent: str = "marcus", business_key: str = "callingdigital") -> list:
    """Push parsed prospects to Attio records.

    If prospect has email, create person record. Otherwise create company record.
    """
    if not attio_ready():
        raise ValueError("Attio credentials not configured. Set ATTIO_API_KEY.")

    results = []
    for p in prospects:
        business_name = p.get("business_name", "")
        try:
            email = (p.get("email") or "").strip()
            if email:
                existing_id = _search_person_by_email(email)
                if existing_id:
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_id,
                        "status": "duplicate_skipped",
                        "provider": "attio",
                    })
                    continue
                rec_id = _create_person_record(p, source_agent, business_key)
            else:
                existing_id = _search_company_by_name(business_name)
                if existing_id:
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_id,
                        "status": "duplicate_skipped",
                        "provider": "attio",
                    })
                    continue
                rec_id = _create_company_record(p, source_agent, business_key)

            results.append({
                "business_name": business_name,
                "contact_id": rec_id,
                "status": "created",
                "email_sent": False,
                "provider": "attio",
            })
        except Exception as e:
            logging.error(f"[Attio] Failed to push prospect {business_name}: {e}")
            results.append({
                "business_name": business_name,
                "status": "failed",
                "provider": "attio",
                "error": str(e),
            })

    return results
