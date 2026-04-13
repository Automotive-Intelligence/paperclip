# AVO — AI Business Operating System
# GHL Contact Enrichment (Verified Process)
# Tavily search → LLM selects (doesn't invent) → write to GHL custom fields
# Salesdroid — April 2026

"""Enrich existing GHL contacts with verified research for Tyler's sequence merge tags.

For each contact, generates 3 pieces of verified intelligence:
  - verified_fact:     A specific verifiable fact from Tavily search
  - trigger_event:     Why NOW is the right time to reach out
  - competitive_insight: What their closest competitor does better

The LLM's ONLY job is to SELECT the best Tavily snippet — never invent or
embellish. Every fact is traceable to a source URL.

Writes to GHL custom fields:
  - contact.verified_fact
  - contact.trigger_event
  - contact.competitive_insight

Usage:
    python scripts/enrich_ghl_contacts.py --dry-run           # Process 5, no writes
    python scripts/enrich_ghl_contacts.py --limit 20          # Process first 20, live
    python scripts/enrich_ghl_contacts.py                     # Full run, all contacts
    python scripts/enrich_ghl_contacts.py --skip-enriched     # Skip contacts that already have verified_fact
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Optional

import requests

GHL_BASE = "https://services.leadconnectorhq.com"


# ── Env ────────────────────────────────────────────────────────────────────

def ghl_api_key() -> str:
    t = (os.getenv("GHL_API_KEY") or "").strip()
    if not t:
        print("ERROR: GHL_API_KEY not set"); sys.exit(1)
    return t


def ghl_location_id() -> str:
    t = (os.getenv("GHL_LOCATION_ID") or "").strip()
    if not t:
        print("ERROR: GHL_LOCATION_ID not set"); sys.exit(1)
    return t


def ghl_headers() -> dict:
    return {
        "Authorization": f"Bearer {ghl_api_key()}",
        "Version": "2021-07-28",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def tavily_key() -> str:
    t = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not t:
        print("ERROR: TAVILY_API_KEY not set"); sys.exit(1)
    return t


def openrouter_key() -> str:
    t = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not t:
        print("ERROR: OPENROUTER_API_KEY not set"); sys.exit(1)
    return t


# ── Tavily ─────────────────────────────────────────────────────────────────

def tavily_search(query: str, max_results: int = 5) -> list:
    """Returns list of {title, url, content} dicts."""
    try:
        r = requests.post("https://api.tavily.com/search", json={
            "api_key": tavily_key(),
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }, timeout=20)
        if r.status_code != 200:
            return []
        return [
            {
                "title": res.get("title", ""),
                "url": res.get("url", ""),
                "content": res.get("content", "")[:500],
            }
            for res in r.json().get("results", [])
        ]
    except Exception:
        return []


# ── LLM extraction (selector, not inventor) ────────────────────────────────

def deepseek_call(prompt: str, max_tokens: int = 200) -> Optional[str]:
    """Call DeepSeek via OpenRouter with retry on rate limits."""
    for attempt in range(3):
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek/deepseek-chat",
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if r.status_code == 429:
                time.sleep(3 + attempt * 3)
                continue
            if r.status_code != 200:
                return None
            return (r.json().get("choices", [{}])[0]
                    .get("message", {}).get("content", "")).strip()
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


def extract_verified_fact(company_name: str, city: str, business_type: str, results: list) -> Optional[str]:
    """Select best Tavily snippet and extract a verifiable fact."""
    if not results:
        return None

    formatted = ""
    for i, r in enumerate(results):
        formatted += f"[RESULT {i}]\nURL: {r['url']}\nTITLE: {r['title']}\nCONTENT: {r['content']}\n\n"

    prompt = f"""I have {len(results)} web search results about "{company_name}", a {business_type} business in {city}.

{formatted}

Your job: Pick the ONE result with the most SPECIFIC, VERIFIABLE fact — a specific location, years in business, recent award, number of reviews, specific service niche, or recent expansion.

Then extract that fact as a SHORT phrase (max 15 words) that is DIRECTLY STATED in the source. Do NOT invent or embellish.

Output format:
FACT: <the extracted fact, max 15 words, directly from the source>

If no specific fact exists (only generic marketing copy), output:
NO_FACT"""

    text = deepseek_call(prompt, max_tokens=100)
    if not text or "NO_FACT" in text:
        return None
    m = re.search(r"FACT:\s*(.+)", text)
    return m.group(1).strip().rstrip(".") if m else None


def extract_trigger_event(company_name: str, city: str, business_type: str, results: list) -> Optional[str]:
    """Select snippet indicating a trigger event — why NOW is the right time."""
    if not results:
        return None

    formatted = ""
    for i, r in enumerate(results):
        formatted += f"[RESULT {i}]\nURL: {r['url']}\nTITLE: {r['title']}\nCONTENT: {r['content']}\n\n"

    prompt = f"""I have {len(results)} web search results about "{company_name}", a {business_type} business in {city}.

{formatted}

Your job: Identify ONE trigger event from the results that signals NOW is the right time to reach out about an AI receptionist.

Trigger events to look for:
- Recent Google reviews mentioning missed calls, voicemail, slow response
- Hiring for front desk or receptionist roles
- New location or expansion
- Seasonal demand (summer AC, storm damage, etc.)
- Negative review streak
- New service launch

Output ONE SHORT sentence (max 20 words) describing the trigger event, directly from the source.

Output format:
TRIGGER: <the trigger event in one sentence>

If no clear trigger event exists, output:
NO_TRIGGER"""

    text = deepseek_call(prompt, max_tokens=100)
    if not text or "NO_TRIGGER" in text:
        return None
    m = re.search(r"TRIGGER:\s*(.+)", text)
    return m.group(1).strip().rstrip(".") if m else None


def extract_competitive_insight(company_name: str, city: str, business_type: str, competitor_results: list) -> Optional[str]:
    """Extract what a local competitor does that this business doesn't."""
    if not competitor_results:
        return None

    formatted = ""
    for i, r in enumerate(competitor_results):
        formatted += f"[RESULT {i}]\nURL: {r['url']}\nTITLE: {r['title']}\nCONTENT: {r['content']}\n\n"

    prompt = f"""I have {len(competitor_results)} web search results about other {business_type} businesses in {city} (competitors of "{company_name}").

{formatted}

Your job: Identify ONE thing a competitor is doing well that would make "{company_name}" want to compete. Focus on:
- Higher review count
- 24/7 availability or after-hours support
- Faster response time
- More reviews than {company_name}

Output ONE SHORT sentence (max 20 words) about what the competitor does better.

Output format:
INSIGHT: <one sentence about what the competitor does better>

If no clear competitive insight, output:
NO_INSIGHT"""

    text = deepseek_call(prompt, max_tokens=100)
    if not text or "NO_INSIGHT" in text:
        return None
    m = re.search(r"INSIGHT:\s*(.+)", text)
    return m.group(1).strip().rstrip(".") if m else None


# ── GHL API ────────────────────────────────────────────────────────────────

def fetch_all_tyler_contacts() -> list:
    """Fetch all contacts tagged with tyler-prospect."""
    all_contacts = []
    params = {"locationId": ghl_location_id(), "limit": 100}
    url = f"{GHL_BASE}/contacts/"

    page = 1
    while True:
        try:
            r = requests.get(url, headers=ghl_headers(), params=params, timeout=30)
            if r.status_code != 200:
                break
            data = r.json()
            contacts = data.get("contacts", [])
            # Filter to only tyler-prospect tagged
            tyler_contacts = [c for c in contacts if "tyler-prospect" in (c.get("tags") or [])]
            all_contacts.extend(tyler_contacts)

            meta = data.get("meta", {})
            if not meta.get("nextPageUrl") or not contacts:
                break

            params["startAfter"] = meta.get("startAfter")
            params["startAfterId"] = meta.get("startAfterId")
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  fetch error: {e}")
            break

    return all_contacts


def get_contact_custom_field(contact: dict, field_key: str) -> str:
    """Extract a custom field value from a GHL contact."""
    for cf in (contact.get("customFields") or []):
        if cf.get("key") == field_key or cf.get("fieldKey") == field_key:
            return cf.get("value") or cf.get("field_value") or ""
    return ""


def update_contact_custom_fields(contact_id: str, fields: dict) -> bool:
    """PATCH a GHL contact with custom field values.

    fields dict maps field_key -> value, e.g.:
        {"contact.trigger_event": "Recent expansion...", ...}
    """
    payload = {
        "customFields": [
            {"key": key, "field_value": value}
            for key, value in fields.items() if value
        ]
    }
    try:
        r = requests.put(
            f"{GHL_BASE}/contacts/{contact_id}",
            headers=ghl_headers(),
            json=payload,
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


# ── Processing ─────────────────────────────────────────────────────────────

def process_contact(contact: dict, dry_run: bool, skip_enriched: bool) -> dict:
    """Enrich one contact. Returns result dict."""
    contact_id = contact.get("id", "")
    name = contact.get("contactName") or contact.get("name", "?")
    company = contact.get("companyName") or ""
    city = contact.get("city") or ""
    business_type = get_contact_custom_field(contact, "contact.business_type") or ""

    # Skip if already enriched
    if skip_enriched:
        existing = get_contact_custom_field(contact, "contact.verified_fact")
        if existing:
            return {"status": "skip", "name": name, "reason": "already enriched"}

    if not company:
        return {"status": "skip", "name": name, "reason": "no company name"}

    # Tavily search for verified fact + trigger event
    q1 = f"{company} {city} {business_type} reviews"
    results_1 = tavily_search(q1, max_results=5)
    time.sleep(0.5)

    # Tavily search for competitor context
    q2 = f"best {business_type} in {city} reviews"
    results_2 = tavily_search(q2, max_results=5)
    time.sleep(0.5)

    # Extract via LLM
    verified_fact = extract_verified_fact(company, city, business_type, results_1)
    trigger_event = extract_trigger_event(company, city, business_type, results_1)
    competitive_insight = extract_competitive_insight(company, city, business_type, results_2)

    fields_to_write = {}
    if verified_fact:
        fields_to_write["contact.verified_fact"] = verified_fact
    if trigger_event:
        fields_to_write["contact.trigger_event"] = trigger_event
    if competitive_insight:
        fields_to_write["contact.competitive_insight"] = competitive_insight

    if not fields_to_write:
        return {"status": "no_data", "name": name, "reason": "no enrichment available"}

    if dry_run:
        return {
            "status": "dry_run",
            "name": name,
            "fields": fields_to_write,
        }

    ok = update_contact_custom_fields(contact_id, fields_to_write)
    return {
        "status": "enriched" if ok else "failed",
        "name": name,
        "fields": fields_to_write,
    }


# ── Driver ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Process limit contacts, no writes")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N contacts")
    parser.add_argument("--skip-enriched", action="store_true", help="Skip contacts that already have verified_fact")
    args = parser.parse_args()

    if args.dry_run and args.limit == 0:
        args.limit = 5

    print("AVO — GHL Contact Enrichment (Verified Process)")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Limit: {args.limit or 'all'}")
    print(f"Skip enriched: {args.skip_enriched}")
    print()

    # Fetch tyler contacts
    print("Fetching Tyler's GHL contacts...")
    contacts = fetch_all_tyler_contacts()
    print(f"Found {len(contacts)} tyler-prospect tagged contacts")
    print()

    if args.limit:
        contacts = contacts[:args.limit]
        print(f"Limited to first {len(contacts)}")
        print()

    # Process each
    results = {"enriched": 0, "skip": 0, "no_data": 0, "failed": 0, "dry_run": 0}
    for i, c in enumerate(contacts, 1):
        name = c.get("contactName") or c.get("name", "?")
        company = c.get("companyName") or ""
        print(f"[{i}/{len(contacts)}] {name} | {company}")

        result = process_contact(c, args.dry_run, args.skip_enriched)
        status = result["status"]
        results[status] = results.get(status, 0) + 1

        if status == "dry_run":
            print(f"  [DRY] would write:")
            for k, v in result.get("fields", {}).items():
                print(f"    {k}: {v[:100]}")
        elif status == "enriched":
            print(f"  ✓ enriched with {len(result.get('fields', {}))} fields")
            for k, v in result.get("fields", {}).items():
                print(f"    {k.split('.')[-1]}: {v[:80]}")
        elif status == "skip":
            print(f"  - skip: {result.get('reason')}")
        elif status == "no_data":
            print(f"  ? no enrichment available")
        elif status == "failed":
            print(f"  ✗ GHL patch failed")

        time.sleep(1.2)  # Rate limit protection

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for key, count in results.items():
        print(f"  {key:12s} {count}")


if __name__ == "__main__":
    main()
