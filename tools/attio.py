"""
tools/attio.py - Attio CRM connector for Paperclip sales pipeline.
"""

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
            if (p.get("email") or "").strip():
                rec_id = _create_person_record(p, source_agent, business_key)
            else:
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
