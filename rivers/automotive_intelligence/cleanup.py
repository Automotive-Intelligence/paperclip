"""HubSpot cleanup script for Automotive Intelligence.

Exports all 690 HubSpot contacts, classifies them by Contact Type,
creates a saved view for Chase, and logs all classifications.
RUN THIS FIRST before any sequences.
"""

import os
import re
import requests
from core.logger import log_info, log_error

HUBSPOT_BASE_URL = "https://api.hubapi.com"


def _hs_key() -> str:
    return (os.environ.get("HUBSPOT_API_KEY") or os.environ.get("HUBSPOT_ACCESS_TOKEN") or "").strip()


def _hs_headers() -> dict:
    return {"Authorization": f"Bearer {_hs_key()}", "Content-Type": "application/json"}

DEALER_DOMAINS = [
    "toyota", "ford", "honda", "chevy", "chevrolet", "nissan", "bmw", "mercedes",
    "volvo", "mazda", "hyundai", "kia", "dodge", "jeep", "subaru", "lexus",
    "acura", "infiniti", "cadillac", "buick", "gmc", "chrysler", "ram",
    "audi", "porsche", "volkswagen", "vw", "mitsubishi", "genesis", "lincoln",
]

DEALER_COMPANY_KEYWORDS = [
    "auto", "motors", "toyota", "ford", "chevy", "chevrolet", "honda", "nissan",
    "bmw", "mercedes", "volvo", "mazda", "hyundai", "kia", "dodge", "jeep",
    "dealer", "group", "subaru", "lexus", "acura", "infiniti", "cadillac",
    "buick", "gmc", "chrysler", "ram", "audi", "porsche", "volkswagen",
]


def ensure_contact_type_property():
    """Create the 'contact_type' property in HubSpot if it doesn't exist."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/properties/contacts"
    payload = {
        "name": "contact_type",
        "label": "Contact Type",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "options": [
            {"label": "Dealership Decision Maker", "value": "dealership_decision_maker"},
            {"label": "Podcast Guest", "value": "podcast_guest"},
            {"label": "Vendor Partner", "value": "vendor_partner"},
            {"label": "Unclassified", "value": "unclassified"},
        ],
    }
    resp = requests.post(url, headers=_hs_headers(), json=payload)
    if resp.status_code == 201:
        log_info("ai_cleanup", "Created 'contact_type' property in HubSpot")
    elif resp.status_code == 409:
        log_info("ai_cleanup", "'contact_type' property already exists")
    else:
        log_error("ai_cleanup", f"Failed to create property: {resp.status_code} {resp.text}")


def fetch_all_contacts() -> list:
    """Fetch all contacts from HubSpot with pagination."""
    contacts = []
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"
    params = {
        "limit": 100,
        "properties": "email,company,firstname,lastname,jobtitle",
    }
    after = None
    while True:
        if after:
            params["after"] = after
        resp = requests.get(url, headers=_hs_headers(), params=params)
        if resp.status_code != 200:
            log_error("ai_cleanup", f"Failed to fetch contacts: {resp.status_code} {resp.text}")
            break
        data = resp.json()
        contacts.extend(data.get("results", []))
        paging = data.get("paging")
        if paging and paging.get("next"):
            after = paging["next"]["after"]
        else:
            break
    log_info("ai_cleanup", f"Fetched {len(contacts)} contacts from HubSpot")
    return contacts


def classify_contact(contact: dict) -> str:
    """Classify a contact based on email domain and company name.
    NOTE: Job Title is intentionally NOT used — it's dirty data (car brands as titles).
    """
    props = contact.get("properties", {})
    email = (props.get("email") or "").lower()
    company = (props.get("company") or "").lower()

    # Check email domain for dealership brands
    domain = email.split("@")[-1] if "@" in email else ""
    for brand in DEALER_DOMAINS:
        if brand in domain:
            return "dealership_decision_maker"

    # Check company name for dealership keywords
    for keyword in DEALER_COMPANY_KEYWORDS:
        if keyword in company:
            return "dealership_decision_maker"

    return "unclassified"


def update_contact_type(contact_id: str, contact_type: str):
    """Update the contact_type property for a contact."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}"
    payload = {"properties": {"contact_type": contact_type}}
    resp = requests.patch(url, headers=_hs_headers(), json=payload)
    if resp.status_code != 200:
        log_error("ai_cleanup", f"Failed to update contact {contact_id}: {resp.status_code}")


def create_saved_view():
    """Create a saved view 'Chase — Verified Dealers' in HubSpot.
    Uses the Lists API to create a contact list filtered by contact_type.
    """
    url = f"{HUBSPOT_BASE_URL}/crm/v3/lists"
    payload = {
        "name": "Chase — Verified Dealers",
        "objectTypeId": "0-1",
        "processingType": "DYNAMIC",
        "filterBranch": {
            "filterBranchType": "AND",
            "filterBranches": [],
            "filters": [
                {
                    "filterType": "PROPERTY",
                    "property": "contact_type",
                    "operation": {
                        "operationType": "ENUMERATION",
                        "operator": "IS_EQUAL_TO",
                        "value": "dealership_decision_maker",
                    },
                }
            ],
        },
    }
    resp = requests.post(url, headers=_hs_headers(), json=payload)
    if resp.status_code in (200, 201):
        log_info("ai_cleanup", "Created saved view: Chase — Verified Dealers")
    else:
        log_info("ai_cleanup", f"View creation response: {resp.status_code} — may already exist")


def run_cleanup():
    """Main cleanup entry point. Run before any sequences."""
    if not _hs_key():
        log_error("ai_cleanup", "HUBSPOT_API_KEY not set — cannot run cleanup")
        return {"classified": 0, "dealers": 0, "unclassified": 0}

    log_info("ai_cleanup", "=== HUBSPOT CLEANUP START ===")

    # Step 1: Ensure property exists
    ensure_contact_type_property()

    # Step 2: Fetch all contacts
    contacts = fetch_all_contacts()

    # Step 3: Classify and update
    stats = {"dealership_decision_maker": 0, "unclassified": 0}
    for contact in contacts:
        cid = contact["id"]
        props = contact.get("properties", {})
        name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or cid
        classification = classify_contact(contact)
        stats[classification] = stats.get(classification, 0) + 1
        update_contact_type(cid, classification)
        log_info("ai_cleanup", f"CLASSIFIED | {cid} | {name} | {classification}")

    # Step 4: Create saved view
    create_saved_view()

    log_info("ai_cleanup", f"=== CLEANUP COMPLETE === Dealers: {stats['dealership_decision_maker']} | Unclassified: {stats['unclassified']}")
    return {
        "classified": len(contacts),
        "dealers": stats["dealership_decision_maker"],
        "unclassified": stats["unclassified"],
    }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    results = run_cleanup()
    print(f"\nCleanup complete: {results}")
