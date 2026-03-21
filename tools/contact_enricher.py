"""
tools/contact_enricher.py — Contact Data Enrichment via Web Search

When sales agents return a prospect without a real email/phone/contact name,
this module uses Tavily to search for that information before the CRM push.

Strategy:
  1. Search "[Business Name] [City] owner contact email"
  2. Extract email, phone, website, contact name from results
  3. Return enriched prospect dict (original fields preserved; blanks filled in)
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
import re
import logging
from typing import Optional


# ── Regex helpers ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)
_PHONE_RE = re.compile(
    r'(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}'
)
_URL_RE = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\](),]+'
)

_SKIP_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "w3.org", "schema.org",
    "wixpress.com", "squarespace.com", "wordpress.com",
}
_SKIP_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster",
    "bounce", "unsubscribe", "info@example",
}
_SKIP_URL_HOSTS = {
    "google.com", "facebook.com", "yelp.com", "linkedin.com",
    "twitter.com", "instagram.com", "youtube.com", "maps.google",
    "bing.com", "yahoo.com", "bbb.org", "yellowpages.com",
}


def _extract_email(text: str) -> str:
    for m in _EMAIL_RE.findall(text or ""):
        lower = m.lower()
        if any(lower.endswith(f"@{d}") for d in _SKIP_EMAIL_DOMAINS):
            continue
        if any(lower.startswith(p) for p in _SKIP_EMAIL_PREFIXES):
            continue
        return m
    return ""


def _extract_phone(text: str) -> str:
    matches = _PHONE_RE.findall(text or "")
    return matches[0].strip() if matches else ""


def _extract_website(text: str) -> str:
    for m in _URL_RE.findall(text or ""):
        url = m.rstrip(".,)")
        host = url.split("/")[2].lstrip("www.") if "//" in url else ""
        if not any(skip in host for skip in _SKIP_URL_HOSTS):
            return url
    # Fallback: return first URL even if it's a directory site
    for m in _URL_RE.findall(text or ""):
        return m.rstrip(".,)")
    return ""


def _extract_contact_name(text: str, business_name: str) -> str:
    """
    Look for name patterns near 'owner', 'manager', 'founder', 'president', 'GM'.
    Returns best guess at a person name, or empty string.
    """
    patterns = [
        r'(?:owner|founder|president|gm|general manager|manager|principal|partner)[,:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})[,:\s]+(?:owner|founder|president|gm|general manager)',
    ]
    for pat in patterns:
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # skip if it's just the business name
            if candidate.lower() not in (business_name or "").lower():
                return candidate
    return ""


# ── Tavily search ─────────────────────────────────────────────────────────────

def _tavily_search(query: str) -> str:
    api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not api_key:
        return ""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=3)
        results = response.get("results", [])
        return "\n\n".join(
            f"TITLE: {r.get('title', '')}\nURL: {r.get('url', '')}\nCONTENT: {r.get('content', '')}"
            for r in results
        )
    except Exception as e:
        logging.warning(f"[Enricher] Tavily search error: {e}")
        return ""


# ── Core enrichment ───────────────────────────────────────────────────────────

def enrich_prospect(prospect: dict) -> dict:
    """
    Given a parsed prospect dict, attempt to fill in missing contact fields
    using Tavily web search. Returns a new dict with gaps filled where possible.

    Fields enriched: contact_name, email, phone, website.
    All original fields are preserved. Only empty/missing fields are updated.
    """
    business_name = (prospect.get("business_name") or "").strip()
    city = (prospect.get("city") or "").strip()

    if not business_name:
        return prospect

    # If we already have an email, enrichment has all the core data we need for sending.
    already_has_email = bool((prospect.get("email") or "").strip())
    already_has_phone = bool((prospect.get("phone") or "").strip())
    already_has_name = bool((prospect.get("contact_name") or "").strip())
    already_has_website = bool((prospect.get("website") or "").strip())

    if already_has_email and already_has_phone and already_has_name:
        return prospect  # already complete

    logging.info(f"[Enricher] Looking up contact info: '{business_name}', {city}")

    # Search 1 — contact/email focus
    search1 = _tavily_search(f'"{business_name}" {city} owner email contact')

    found_email = _extract_email(search1) if not already_has_email else ""
    found_phone = _extract_phone(search1) if not already_has_phone else ""
    found_website = _extract_website(search1) if not already_has_website else ""
    found_name = _extract_contact_name(search1, business_name) if not already_has_name else ""

    # Search 2 — website/phone focus if still missing
    if not found_email and not found_website:
        search2 = _tavily_search(f'"{business_name}" {city} website phone number')
        if not found_phone:
            found_phone = _extract_phone(search2)
        if not found_website:
            found_website = _extract_website(search2)
        if not found_name:
            found_name = _extract_contact_name(search2, business_name)

    updated = dict(prospect)
    if found_email:
        updated["email"] = found_email
        logging.info(f"[Enricher] Found email for '{business_name}': {found_email}")
    if found_phone and not already_has_phone:
        updated["phone"] = found_phone
        logging.info(f"[Enricher] Found phone for '{business_name}': {found_phone}")
    if found_website and not already_has_website:
        updated["website"] = found_website
    if found_name and not already_has_name:
        updated["contact_name"] = found_name
        logging.info(f"[Enricher] Found contact name for '{business_name}': {found_name}")

    return updated


def enrich_prospects(prospects: list, only_missing_email: bool = True) -> list:
    """
    Enrich a list of prospect dicts. Runs sequentially to avoid Tavily rate limits.

    Args:
        prospects: list of parsed prospect dicts
        only_missing_email: if True (default), only enrich prospects with no email.
                            if False, enrich all (fills name/phone/website too).
    """
    if not (os.environ.get("TAVILY_API_KEY") or "").strip():
        logging.info("[Enricher] TAVILY_API_KEY not set — skipping contact enrichment.")
        return prospects

    enriched = []
    for p in prospects:
        try:
            has_email = bool((p.get("email") or "").strip())
            if only_missing_email and has_email:
                # Still tag what we have so CRM get the full picture
                enriched.append(p)
                continue
            enriched.append(enrich_prospect(p))
        except Exception as e:
            logging.warning(f"[Enricher] Failed to enrich '{p.get('business_name')}': {e}")
            enriched.append(p)

    found_emails = sum(1 for p in enriched if (p.get("email") or "").strip())
    logging.info(f"[Enricher] Enrichment complete: {found_emails}/{len(enriched)} prospects have emails.")
    return enriched
