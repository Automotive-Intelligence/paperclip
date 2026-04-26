"""tools/klaviyo.py — Klaviyo email tools for Sofia.

Wraps Klaviyo's Public API (revision 2024-10-15) for email sequence work
on the Paper and Purpose campaign and any other CD-agency-managed tenants.

Phase 1 scope is "draft + queue, never send." Sofia drafts welcome sequences
(#5), backer updates (#11), and urgency close emails (#12) in this tool, but
flow activation always goes through the approval digest gate — Miriam taps
Approve in the digest email before any sequence fires. This module
deliberately does NOT expose flow activation or campaign send endpoints.

Per-tenant credential resolution (matches tools/outbound_email pattern):
  KLAVIYO_API_KEY_<BUSINESSKEY>   private Klaviyo API key (pk_...)

CD agency note: Klaviyo's partner program does not expose a single agency-
level API key that operates on child accounts. Each child account has its
own private API key. CD-the-agency holds those keys on behalf of clients
(P&P uses its own Klaviyo account, CD operates inside it). This module
treats every tenant's Klaviyo account as its own credential silo.

Tools exposed:
  - klaviyo_list_lists(business_key)
  - klaviyo_list_segments(business_key)
  - klaviyo_create_list(business_key, list_name)
  - klaviyo_add_profile_to_list(business_key, list_id, email, first_name, last_name)
  - klaviyo_list_templates(business_key)
  - klaviyo_create_template(business_key, name, html_body, subject)

Errors return as strings (matches web_search_tool / tools/keyapi.py pattern)
so the LLM gets useful feedback instead of a crashed Crew.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests
from crewai.tools import tool

logger = logging.getLogger(__name__)

KLAVIYO_BASE_URL = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"
DEFAULT_TIMEOUT = 30


def _suffix(business_key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (business_key or "").strip().lower()).upper()


def _api_key_for(business_key: str) -> str | None:
    suffix = _suffix(business_key)
    if not suffix:
        return None
    val = (os.environ.get(f"KLAVIYO_API_KEY_{suffix}") or "").strip()
    return val or None


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "revision": KLAVIYO_REVISION,
    }


def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
    out = json.dumps(obj, indent=2, default=str)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[...truncated for context budget...]"
    return out


def _klaviyo_request(
    method: str,
    business_key: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | str:
    """Low-level Klaviyo HTTP. Returns parsed JSON dict on success, or a
    human-readable error string on failure. Never raises."""
    api_key = _api_key_for(business_key)
    if not api_key:
        return f"ERROR: KLAVIYO_API_KEY_{_suffix(business_key)} env var not set."

    url = f"{KLAVIYO_BASE_URL}/{path.lstrip('/')}"
    try:
        resp = requests.request(
            method,
            url,
            headers=_headers(api_key),
            params=params or {},
            json=json_body if json_body is not None else None,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return f"ERROR: Klaviyo timeout on {method} {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: Klaviyo request failed on {method} {path}: {type(e).__name__}: {e}"

    if resp.status_code in (401, 403):
        return f"ERROR: Klaviyo rejected the API key (HTTP {resp.status_code}). Verify KLAVIYO_API_KEY_{_suffix(business_key)} is valid and has the required scopes."
    if resp.status_code == 429:
        return "ERROR: Klaviyo rate limit hit (429). Wait a few seconds and retry."
    if resp.status_code >= 400:
        return f"ERROR: Klaviyo HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}"

    if resp.status_code == 204 or not resp.content:
        return {"ok": True, "status": resp.status_code}

    try:
        return resp.json()
    except ValueError:
        return f"ERROR: Klaviyo returned non-JSON on {method} {path}: {resp.text[:300]}"


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@tool("List Klaviyo Lists")
def klaviyo_list_lists(business_key: str) -> str:
    """Return all subscriber lists configured in the tenant's Klaviyo account.

    Use this before drafting a welcome sequence so you can target the right
    list. Returns list IDs, names, created/updated timestamps, and counts.

    Args:
        business_key: Tenant key (e.g. 'paper_and_purpose').

    Returns: Lists data as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    if not business_key:
        return "ERROR: business_key is required."
    resp = _klaviyo_request("GET", business_key, "lists/")
    if isinstance(resp, str):
        return resp
    rows = [
        {
            "id": item.get("id"),
            "name": (item.get("attributes") or {}).get("name"),
            "created": (item.get("attributes") or {}).get("created"),
            "updated": (item.get("attributes") or {}).get("updated"),
            "opt_in_process": (item.get("attributes") or {}).get("opt_in_process"),
        }
        for item in (resp.get("data") or [])
    ]
    return _truncate_json({"business_key": business_key, "lists": rows})


@tool("List Klaviyo Segments")
def klaviyo_list_segments(business_key: str) -> str:
    """Return all segments in the tenant's Klaviyo account.

    Segments are dynamic, condition-based audiences (e.g. "engaged in last 30 days").
    Use to target sequences at specific behavioral cohorts rather than static lists.

    Args:
        business_key: Tenant key.

    Returns: Segments data as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    if not business_key:
        return "ERROR: business_key is required."
    resp = _klaviyo_request("GET", business_key, "segments/")
    if isinstance(resp, str):
        return resp
    rows = [
        {
            "id": item.get("id"),
            "name": (item.get("attributes") or {}).get("name"),
            "created": (item.get("attributes") or {}).get("created"),
            "updated": (item.get("attributes") or {}).get("updated"),
            "is_active": (item.get("attributes") or {}).get("is_active"),
        }
        for item in (resp.get("data") or [])
    ]
    return _truncate_json({"business_key": business_key, "segments": rows})


@tool("List Klaviyo Email Templates")
def klaviyo_list_templates(business_key: str) -> str:
    """Return saved email templates in the tenant's Klaviyo account.

    Useful for inspecting prior templates before authoring new ones, so the
    new sequence visually matches what's already there.

    Args:
        business_key: Tenant key.

    Returns: Templates data as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    if not business_key:
        return "ERROR: business_key is required."
    resp = _klaviyo_request("GET", business_key, "templates/")
    if isinstance(resp, str):
        return resp
    rows = [
        {
            "id": item.get("id"),
            "name": (item.get("attributes") or {}).get("name"),
            "editor_type": (item.get("attributes") or {}).get("editor_type"),
            "created": (item.get("attributes") or {}).get("created"),
            "updated": (item.get("attributes") or {}).get("updated"),
        }
        for item in (resp.get("data") or [])
    ]
    return _truncate_json({"business_key": business_key, "templates": rows})


# ---------------------------------------------------------------------------
# Write tools (drafts only — no campaign sends, no flow activations)
# ---------------------------------------------------------------------------

@tool("Create Klaviyo List")
def klaviyo_create_list(business_key: str, list_name: str) -> str:
    """Create a new subscriber list in the tenant's Klaviyo account.

    Use to scaffold campaign-specific lists (e.g. "Pre-launch interest list",
    "Backers", "Urgency close cohort"). Returns the new list ID and metadata.

    Args:
        business_key: Tenant key.
        list_name: Human-readable list name.

    Returns: New list data as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    list_name = (list_name or "").strip()
    if not business_key:
        return "ERROR: business_key is required."
    if not list_name:
        return "ERROR: list_name is required."
    resp = _klaviyo_request(
        "POST",
        business_key,
        "lists/",
        json_body={
            "data": {
                "type": "list",
                "attributes": {"name": list_name},
            }
        },
    )
    if isinstance(resp, str):
        return resp
    return _truncate_json({"business_key": business_key, "created_list": resp.get("data") or {}})


@tool("Add Profile to Klaviyo List")
def klaviyo_add_profile_to_list(
    business_key: str,
    list_id: str,
    email: str,
    first_name: str = "",
    last_name: str = "",
) -> str:
    """Add a single profile (subscriber) to a Klaviyo list.

    Use sparingly during research/testing. The bulk subscriber import path
    is owned by the storefront's native Shopify→Klaviyo integration; this
    tool is for one-off operations (e.g. seeding the team's own emails into
    the pre-launch list to test the welcome sequence renders correctly).

    Args:
        business_key: Tenant key.
        list_id: Klaviyo list ID (from klaviyo_list_lists).
        email: Subscriber email address.
        first_name: Optional first name.
        last_name: Optional last name.

    Returns: Operation result as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    list_id = (list_id or "").strip()
    email = (email or "").strip()
    if not (business_key and list_id and email):
        return "ERROR: business_key, list_id, and email are all required."

    profile_attrs: dict[str, Any] = {"email": email}
    if first_name:
        profile_attrs["first_name"] = first_name
    if last_name:
        profile_attrs["last_name"] = last_name

    resp = _klaviyo_request(
        "POST",
        business_key,
        f"lists/{list_id}/relationships/profiles/",
        json_body={
            "data": [
                {
                    "type": "profile",
                    "attributes": profile_attrs,
                }
            ]
        },
    )
    if isinstance(resp, str):
        return resp
    return _truncate_json({"business_key": business_key, "list_id": list_id, "result": resp})


@tool("Create Klaviyo Email Template")
def klaviyo_create_template(
    business_key: str,
    name: str,
    html_body: str,
    subject: str = "",
) -> str:
    """Create a new email template in the tenant's Klaviyo account.

    The template is saved as a draft — no send happens. Use this to draft
    welcome sequence emails (#5), backer update templates (#11), and urgency
    close emails (#12) for the campaign. After creation, the template ID can
    be referenced from a Flow or Campaign — but flow activation / campaign
    send is intentionally NOT exposed in this module. Activation goes through
    the approval digest gate.

    Args:
        business_key: Tenant key.
        name: Template name (e.g. 'P&P Welcome 1 — first hello').
        html_body: HTML body of the email.
        subject: Default subject line saved with the template (optional).

    Returns: New template data as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    name = (name or "").strip()
    html_body = (html_body or "").strip()
    if not (business_key and name and html_body):
        return "ERROR: business_key, name, and html_body are all required."

    attrs: dict[str, Any] = {
        "name": name,
        "html": html_body,
        "editor_type": "CODE",
    }
    if subject:
        attrs["subject"] = subject

    resp = _klaviyo_request(
        "POST",
        business_key,
        "templates/",
        json_body={
            "data": {
                "type": "template",
                "attributes": attrs,
            }
        },
    )
    if isinstance(resp, str):
        return resp
    return _truncate_json({"business_key": business_key, "created_template": resp.get("data") or {}})


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def klaviyo_status() -> dict[str, Any]:
    """Lightweight observability for /admin endpoints.
    Reports which tenants have a Klaviyo API key configured."""
    configured: dict[str, bool] = {}
    for k, v in os.environ.items():
        if k.startswith("KLAVIYO_API_KEY_"):
            suffix = k.removeprefix("KLAVIYO_API_KEY_")
            configured[suffix] = bool((v or "").strip())
    return {
        "api_revision": KLAVIYO_REVISION,
        "tenants": configured,
    }


KLAVIYO_TOOLS = [
    klaviyo_list_lists,
    klaviyo_list_segments,
    klaviyo_list_templates,
    klaviyo_create_list,
    klaviyo_add_profile_to_list,
    klaviyo_create_template,
]
