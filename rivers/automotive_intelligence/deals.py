"""HubSpot deal creation for Automotive Intelligence (Darrell).

On classification as Dealership Decision Maker:
- Creates HubSpot deal at Stage 1 "Qualified Lead"
- Associates deal with contact
"""

import os
import requests
from core.logger import log_info, log_error

HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY")
BASE_URL = "https://api.hubapi.com"
HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def create_deal(contact_id: str, contact_name: str, company: str) -> str | None:
    """Create a HubSpot deal for a verified dealer contact."""
    if not HUBSPOT_API_KEY:
        log_info("automotive_intelligence", f"[DRY RUN] Would create deal for {contact_name}")
        return None

    deal_name = f"AI Audit — {company or contact_name}"

    payload = {
        "properties": {
            "dealname": deal_name,
            "dealstage": "qualifiedtobuy",
            "pipeline": "default",
            "amount": "997",
        },
    }

    try:
        resp = requests.post(f"{BASE_URL}/crm/v3/objects/deals", headers=HEADERS, json=payload)
        if resp.status_code in (200, 201):
            deal_id = resp.json().get("id")
            log_info("automotive_intelligence", f"Created deal {deal_id}: {deal_name}")

            # Associate deal with contact
            assoc_url = f"{BASE_URL}/crm/v3/objects/deals/{deal_id}/associations/contacts/{contact_id}/deal_to_contact"
            requests.put(assoc_url, headers=HEADERS)
            log_info("automotive_intelligence", f"Associated deal {deal_id} with contact {contact_id}")
            return deal_id
        else:
            log_error("automotive_intelligence", f"Deal creation failed: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        log_error("automotive_intelligence", f"Deal creation error: {e}")
        return None
