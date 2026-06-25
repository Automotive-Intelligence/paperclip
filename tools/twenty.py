"""tools/twenty.py — Twenty CRM writer client.

Phase 1 of the Twenty-writer initiative (docs/superpowers/plans/
2026-06-25-twenty-crm-writer-attio-hubspot-retirement.md). Mirrors the
push_prospects_to_hubspot contract so tools/crm_router.py can swap branches
in Phase 2 without callers noticing.

Per-business workspace routing — Twenty is multi-tenant by workspace, so
each business writes to its OWN workspace using its OWN API key:

  callingdigital   → WD workspace      (TWENTY_WD_URL,    TWENTY_WD_API_KEY)
  autointelligence → AvI workspace     (TWENTY_AVI_API_URL, TWENTY_AVI_API_KEY)
  bookd            → Book'd workspace  (TWENTY_BOOKD_URL, TWENTY_BOOKD_API_KEY)

REST API surface (Twenty exposes both REST + GraphQL — REST is simpler for
write-only and matches the request shape used elsewhere in tools/):
  POST /rest/people     — create a person
  GET  /rest/people     — search (filter by email)
  POST /rest/companies  — create a company
  GET  /rest/companies  — search (filter by name)
  POST /rest/opportunities — create an opportunity (deal analog)

Email send is intentionally OUT of Phase 1 — Twenty has no transactional-email
endpoint, and the unified-email path used elsewhere is owned by the crm_router
wiring (Phase 2). Result-dict still includes the email_attempted/email_sent
keys so the consumer contract stays identical to the HubSpot writer.
"""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


def _normalize_phone_e164(raw: str) -> Optional[str]:
    """Best-effort US-default E.164 normalizer.

    Twenty's REST validator (`INVALID_PHONE_NUMBER`) rejects loose US formats
    like `212-555-0100` or `(888) 248-0307`. Send E.164 or omit.

    Returns the +E.164 string, or None if the input can't be normalized
    (caller should omit the phones field entirely on None).
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if raw.lstrip().startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def _twenty_error_detail(resp: requests.Response) -> str:
    """Pull Twenty's JSON `messages` / `error` into the exception text.

    `raise_for_status()` alone only surfaces the URL — Twenty's body holds the
    real reason (e.g. `INVALID_PHONE_NUMBER`, `Invalid UUID value ''`).
    """
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    msgs = body.get("messages") or []
    if isinstance(msgs, list) and msgs:
        return f"{body.get('code') or body.get('error') or 'error'}: {'; '.join(str(m) for m in msgs)}"
    return body.get("message") or body.get("error") or str(body)[:300]


def _raise_with_body(resp: requests.Response, endpoint: str) -> None:
    """Like raise_for_status() but the message includes Twenty's body."""
    if resp.ok:
        return
    raise requests.HTTPError(
        f"{resp.status_code} {endpoint} — {_twenty_error_detail(resp)}",
        response=resp,
    )


# ── Per-workspace config ────────────────────────────────────────────────────

# Default workspace URLs. Overridable via env for non-default deployments
# (e.g. self-hosted instances on a different domain). WD has its own
# brand-stable URL; AvI/Book'd live on Railway-issued URLs that can change,
# hence URL-via-env for those two.
_DEFAULT_WORKSPACE_URLS = {
    "callingdigital":   "https://crm.worshipdigital.co",
    "autointelligence": "",  # must come from env
    "bookd":            "https://bookd.twenty.com",
}

_WORKSPACE_URL_ENV = {
    "callingdigital":   "TWENTY_WD_URL",
    "autointelligence": "TWENTY_AVI_API_URL",
    "bookd":            "TWENTY_BOOKD_URL",
}

_WORKSPACE_KEY_ENV = {
    "callingdigital":   "TWENTY_WD_API_KEY",
    "autointelligence": "TWENTY_AVI_API_KEY",
    "bookd":            "TWENTY_BOOKD_API_KEY",
}

_REQUEST_TIMEOUT = 15


def _workspace_config(business_key: str) -> Tuple[str, str]:
    """Return (base_url, api_key) for the business's Twenty workspace.

    Raises ValueError if the workspace isn't configured for this business —
    same failure shape as hubspot_ready/attio_ready guards in their writers.
    """
    bk = (business_key or "").strip().lower()
    if bk not in _WORKSPACE_KEY_ENV:
        raise ValueError(
            f"Twenty: no workspace mapping for business_key={business_key!r}. "
            f"Known: {', '.join(_WORKSPACE_KEY_ENV)}"
        )
    api_key = (os.getenv(_WORKSPACE_KEY_ENV[bk]) or "").strip()
    if not api_key:
        raise ValueError(
            f"Twenty: {_WORKSPACE_KEY_ENV[bk]} not set for business {bk!r}"
        )
    base_url = (os.getenv(_WORKSPACE_URL_ENV[bk]) or _DEFAULT_WORKSPACE_URLS[bk]).strip().rstrip("/")
    if not base_url:
        raise ValueError(
            f"Twenty: {_WORKSPACE_URL_ENV[bk]} not set for business {bk!r}"
        )
    return base_url, api_key


def twenty_ready(business_key: str) -> bool:
    """True iff the business's Twenty workspace has URL + API key set.

    Used by config/runtime.py + crm_router.crm_status_snapshot(). Per-business
    because Twenty is multi-tenant — global "twenty_ready" doesn't make sense.
    """
    try:
        _workspace_config(business_key)
        return True
    except ValueError:
        return False


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


# ── Search helpers (idempotency) ────────────────────────────────────────────


def _search_person_by_email(email: str, base_url: str, api_key: str) -> Optional[str]:
    """Return existing Twenty person id matching this email, or None."""
    email = (email or "").strip()
    if not email:
        return None
    try:
        r = requests.get(
            f"{base_url}/rest/people",
            headers=_headers(api_key),
            params={
                "filter": f"emails.primaryEmail[eq]:{email}",
                "limit": 1,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        people = (r.json().get("data") or {}).get("people") or []
        return people[0].get("id") if people else None
    except Exception as e:
        logger.warning("[Twenty] person search failed for %s: %s", email, e)
        return None


def _search_company_by_name(name: str, base_url: str, api_key: str) -> Optional[str]:
    """Return existing Twenty company id matching this name, or None."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        r = requests.get(
            f"{base_url}/rest/companies",
            headers=_headers(api_key),
            params={
                "filter": f"name[eq]:{name}",
                "limit": 1,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        companies = (r.json().get("data") or {}).get("companies") or []
        return companies[0].get("id") if companies else None
    except Exception as e:
        logger.warning("[Twenty] company search failed for %s: %s", name, e)
        return None


def _domain_host(raw: str) -> str:
    """Reduce a URL/domain string to its bare host: lowercase, no scheme,
    no www., no path, no trailing slash. Returns '' if empty/unparseable.

    Used for company-dedup search via Twenty's `domainName.primaryLinkUrl[ilike]:%host%`,
    which is required because Twenty's unique constraint lives on the raw URL —
    so `https://nycjeep.com/` and `http://www.nycjeep.com` collide as duplicates
    but a name[eq] search won't find either if the stored record is named
    differently from the new prospect.
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


def _search_company_by_domain(domain_raw: str, base_url: str, api_key: str) -> Optional[str]:
    """Return existing Twenty company id whose primaryLinkUrl contains this
    host, or None. Tolerates scheme + www + path variance via host-only ilike.
    """
    host = _domain_host(domain_raw)
    if not host:
        return None
    try:
        r = requests.get(
            f"{base_url}/rest/companies",
            headers=_headers(api_key),
            params={
                "filter": f"domainName.primaryLinkUrl[ilike]:%{host}%",
                "limit": 1,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        companies = (r.json().get("data") or {}).get("companies") or []
        return companies[0].get("id") if companies else None
    except Exception as e:
        logger.warning("[Twenty] company domain-search failed for %s: %s", host, e)
        return None


# ── Create helpers ──────────────────────────────────────────────────────────


def _split_name(prospect: dict) -> Tuple[str, str]:
    """Split prospect contact-name into (first, last). Falls back to splitting
    `contact` on whitespace, then to (business_name, '') if no contact at all.
    """
    contact = (prospect.get("contact") or "").strip()
    if contact:
        parts = contact.split(None, 1)
        return (parts[0], parts[1] if len(parts) > 1 else "")
    return ("", "")


def _create_company(prospect: dict, base_url: str, api_key: str) -> str:
    """Create a Twenty company record and return its id.

    On 400 'A duplicate entry was detected' (Twenty enforces uniqueness on
    `domainName.primaryLinkUrl`), fall back to a host-ilike domain search and
    return the existing record's id. This handles the race where the prospect
    has a different business_name than the stored record but the same domain
    (e.g. stored as "CDRF of Manhattan & Jeep of Manhattan" with nycjeep.com,
    prospect arrives as "Jeep of Manhattan" with nycjeep.com).
    """
    payload = {
        "name": prospect.get("business_name", "") or "",
    }
    domain = (prospect.get("website") or prospect.get("domain") or "").strip()
    if domain:
        payload["domainName"] = {"primaryLinkUrl": domain, "primaryLinkLabel": ""}
    city = (prospect.get("city") or "").strip()
    if city:
        payload["address"] = {"addressCity": city}

    r = requests.post(
        f"{base_url}/rest/companies",
        headers=_headers(api_key),
        json=payload,
        timeout=_REQUEST_TIMEOUT,
    )
    if r.status_code == 400 and "duplicate entry" in r.text.lower():
        existing = _search_company_by_domain(domain, base_url, api_key)
        if existing:
            logger.info(
                "[Twenty] company duplicate on domain %r — reusing existing id=%s",
                _domain_host(domain), existing,
            )
            return existing
        logger.warning(
            "[Twenty] duplicate-entry on create for %r but domain-search came up empty (domain=%r)",
            payload.get("name"), domain,
        )
    _raise_with_body(r, "POST /rest/companies")
    company = (r.json().get("data") or {}).get("createCompany") or {}
    return company.get("id", "")


def _create_person(
    prospect: dict,
    base_url: str,
    api_key: str,
    company_id: Optional[str] = None,
) -> str:
    """Create a Twenty person record and return its id."""
    first, last = _split_name(prospect)
    payload: Dict = {
        "name": {"firstName": first, "lastName": last},
    }
    email = (prospect.get("email") or "").strip()
    if email:
        payload["emails"] = {"primaryEmail": email, "additionalEmails": []}
    phone_e164 = _normalize_phone_e164((prospect.get("phone") or "").strip())
    if phone_e164:
        payload["phones"] = {
            "primaryPhoneNumber": phone_e164,
            "primaryPhoneCountryCode": "",
            "primaryPhoneCallingCode": "",
            "additionalPhones": [],
        }
    job_title = (prospect.get("job_title") or prospect.get("title") or "").strip()
    if job_title:
        payload["jobTitle"] = job_title
    if company_id and isinstance(company_id, str) and company_id.strip():
        payload["companyId"] = company_id.strip()

    r = requests.post(
        f"{base_url}/rest/people",
        headers=_headers(api_key),
        json=payload,
        timeout=_REQUEST_TIMEOUT,
    )
    _raise_with_body(r, "POST /rest/people")
    person = (r.json().get("data") or {}).get("createPerson") or {}
    return person.get("id", "")


def _create_opportunity(
    prospect: dict,
    source_agent: str,
    base_url: str,
    api_key: str,
    person_id: Optional[str] = None,
    company_id: Optional[str] = None,
) -> Optional[str]:
    """Create a Twenty opportunity (deals analog). Returns id, or None on failure.

    Failure here does NOT raise — opportunity creation is best-effort. The
    person/company record is the load-bearing write; an opportunity not landing
    is recoverable (re-runnable), but losing the person/company would be silent
    lead loss.
    """
    name = prospect.get("business_name", "") or prospect.get("contact", "") or "Opportunity"
    payload: Dict = {"name": f"{name} ({source_agent})"}
    if person_id and isinstance(person_id, str) and person_id.strip():
        payload["pointOfContactId"] = person_id.strip()
    if company_id and isinstance(company_id, str) and company_id.strip():
        payload["companyId"] = company_id.strip()
    try:
        r = requests.post(
            f"{base_url}/rest/opportunities",
            headers=_headers(api_key),
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )
        _raise_with_body(r, "POST /rest/opportunities")
        opp = (r.json().get("data") or {}).get("createOpportunity") or {}
        return opp.get("id")
    except Exception as e:
        logger.warning("[Twenty] opportunity create failed for %s: %s", name, e)
        return None


# ── Public entry ────────────────────────────────────────────────────────────


def push_prospects_to_twenty(
    prospects: list,
    source_agent: str = "marcus",
    business_key: str = "callingdigital",
) -> list:
    """Push parsed prospects to Twenty as people (preferred) or companies.

    Contract MIRRORS push_prospects_to_hubspot for drop-in router replacement.
    Each result dict has keys: business_name, contact_id, status,
    email_attempted, email_sent, template_key, template_valid, template_issues,
    deal_created, provider, contact_email (when person path), error (on failed).

    Email-send is OUT of Phase 1 — email_attempted/email_sent always False.
    Phase 2 wires the unified-email path through crm_router.
    """
    base_url, api_key = _workspace_config(business_key)  # raises if unconfigured

    results = []
    for p in prospects:
        business_name = p.get("business_name", "") or ""
        try:
            email = (p.get("email") or "").strip()
            if email:
                existing_id = _search_person_by_email(email, base_url, api_key)
                if existing_id:
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_id,
                        "status": "duplicate_skipped",
                        "contact_email": email,
                        "email_attempted": False,
                        "email_sent": False,
                        "template_key": "",
                        "template_valid": True,
                        "template_issues": [],
                        "provider": "twenty",
                    })
                    continue

                # Person-first path: create company if business_name present,
                # then person linked to it. Mirrors Attio writer's pattern.
                company_id: Optional[str] = None
                if business_name:
                    existing_company = (
                        _search_company_by_name(business_name, base_url, api_key)
                        or _search_company_by_domain(
                            (p.get("website") or p.get("domain") or ""), base_url, api_key
                        )
                    )
                    company_id = existing_company or _create_company(p, base_url, api_key)
                person_id = _create_person(p, base_url, api_key, company_id=company_id)
                opp_id = _create_opportunity(
                    p, source_agent, base_url, api_key,
                    person_id=person_id, company_id=company_id,
                )

                results.append({
                    "business_name": business_name,
                    "contact_id": person_id,
                    "status": "created",
                    "contact_email": email,
                    "email_attempted": False,
                    "email_sent": False,
                    "template_key": "",
                    "template_valid": True,
                    "template_issues": [],
                    "deal_created": bool(opp_id),
                    "provider": "twenty",
                })
                continue

            # No email — company-only path
            existing_company_id = (
                _search_company_by_name(business_name, base_url, api_key)
                or _search_company_by_domain(
                    (p.get("website") or p.get("domain") or ""), base_url, api_key
                )
            )
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
                    "provider": "twenty",
                })
                continue

            new_company_id = _create_company(p, base_url, api_key)
            opp_id = _create_opportunity(
                p, source_agent, base_url, api_key, company_id=new_company_id,
            )
            results.append({
                "business_name": business_name,
                "contact_id": new_company_id,
                "status": "created",
                "email_attempted": False,
                "email_sent": False,
                "template_key": "",
                "template_valid": True,
                "template_issues": [],
                "deal_created": bool(opp_id),
                "provider": "twenty",
            })
        except Exception as e:
            logger.error("[Twenty] Failed to push prospect %s: %s", business_name, e)
            results.append({
                "business_name": business_name,
                "status": "failed",
                "email_attempted": False,
                "email_sent": False,
                "template_key": "",
                "template_valid": False,
                "template_issues": ["send_path_failed"],
                "provider": "twenty",
                "error": str(e),
            })

    return results
