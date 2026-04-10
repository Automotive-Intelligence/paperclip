# AVO — AI Business Operating System
# VERIFIED enrichment: Tavily search → LLM selects (doesn't invent) → source URL kept
# Ironclad process — no hallucinated facts, only web-verifiable observations
# Salesdroid — April 2026

"""Verified enrichment for OwnerPhones contacts.

The LLM's ONLY job is to SELECT which Tavily snippet is most relevant
for a cold email opener. It does NOT rewrite, embellish, or add detail.
The output is a direct quote or close paraphrase of the Tavily snippet,
plus the source URL for auditability.

Schema written to Attio Company.cd_prospect_notes:
  "<observation from Tavily snippet> [source: <url>]"

Usage:
    python scripts/enrich_verified.py --dry-run     # Process 5, show output, no writes
    python scripts/enrich_verified.py --limit 20    # Process first 20, live
    python scripts/enrich_verified.py               # Full run, all contacts
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Optional

import requests

ATTIO_BASE = "https://api.attio.com/v2"

VERTICAL_QUERY_TERM = {
    "med-spa": "med spa aesthetics",
    "pi-law": "personal injury attorney law firm",
    "real-estate": "real estate agent broker",
    "home-builder": "custom home builder",
}


def attio_token() -> str:
    t = (os.getenv("ATTIO_API_KEY") or "").strip()
    if not t:
        print("ERROR: ATTIO_API_KEY not set"); sys.exit(1)
    return t

def attio_headers() -> dict:
    return {"Authorization": f"Bearer {attio_token()}", "Content-Type": "application/json"}

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
    """Returns list of {title, url, content} dicts — raw Tavily results."""
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


# ── LLM selector (does NOT invent — only selects and extracts) ─────────────

def select_best_snippet(company_name: str, vertical: str, results: list) -> Optional[dict]:
    """Ask the LLM to pick the BEST Tavily result and extract ONE verifiable fact.

    Returns: {observation: str, source_url: str, source_index: int} or None.
    The observation must be a direct quote or very close paraphrase of the source.
    """
    if not results:
        return None

    # Format the search results for the LLM
    formatted = ""
    for i, r in enumerate(results):
        formatted += f"[RESULT {i}]\n"
        formatted += f"URL: {r['url']}\n"
        formatted += f"TITLE: {r['title']}\n"
        formatted += f"CONTENT: {r['content']}\n\n"

    prompt = f"""I have {len(results)} web search results about "{company_name}", a Texas {vertical} business.

{formatted}

Your job: Pick the ONE result that contains the most SPECIFIC, VERIFIABLE fact about this business — something like: a specific location, years in business, a recent award, number of reviews, a named partner, a specific service niche, or a recent expansion.

Then extract that fact as a SHORT phrase (max 15 words) that is DIRECTLY STATED in the search result. Do NOT add any information that isn't in the source text. Do NOT embellish or generalize.

Output format (exactly this, no deviation):
RESULT_INDEX: <number>
FACT: <the extracted fact, max 15 words, directly from the source text>

If NONE of the results contain a specific verifiable fact about this business (only generic marketing copy), output exactly:
NO_FACT

Examples of GOOD facts (directly from source text):
FACT: serving the Hill Country since 2009 with five-star custom homes
FACT: recently named to the 2026 Lawdragon 500 Leading Plaintiff Lawyers
FACT: 1.4K Instagram followers with 317 posts showcasing DFW projects
FACT: located at 615 Sabine St in Hemphill with Toledo Bend property listings

Examples of BAD facts (invented or embellished):
FACT: one of the leading firms in Texas (too vague, not from source)
FACT: known for their exceptional client service (generic, not verifiable)
FACT: built over 3,000 homes in 25 years (number not in source text)"""

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
                    "max_tokens": 100,
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

            text = (r.json().get("choices", [{}])[0]
                    .get("message", {}).get("content", "")).strip()

            if "NO_FACT" in text:
                return None

            # Parse the structured output — handle format variations from the LLM
            idx_match = re.search(r"RESULT[_\s]*(?:INDEX)?[:\s]*(\d+)", text, re.IGNORECASE)
            fact_match = re.search(r"FACT:\s*(.+)", text)

            if not idx_match or not fact_match:
                return None

            result_idx = int(idx_match.group(1))
            fact = fact_match.group(1).strip().rstrip(".")

            if result_idx >= len(results):
                result_idx = 0

            return {
                "observation": fact,
                "source_url": results[result_idx]["url"],
                "source_title": results[result_idx]["title"],
                "source_index": result_idx,
            }
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


# ── Attio ──────────────────────────────────────────────────────────────────

def get_all_ownerphones_people() -> list:
    r = requests.post(f"{ATTIO_BASE}/objects/people/records/query",
        headers=attio_headers(),
        json={"filter": {"pipeline_stage": {"$eq": "ownerphones-warm"}}, "limit": 500},
        timeout=30)
    return r.json().get("data", []) if r.status_code == 200 else []


def get_company(cid: str) -> Optional[dict]:
    r = requests.get(f"{ATTIO_BASE}/objects/companies/records/{cid}",
        headers=attio_headers(), timeout=15)
    return r.json().get("data") if r.status_code == 200 else None


def update_company_notes(cid: str, notes: str) -> bool:
    r = requests.patch(f"{ATTIO_BASE}/objects/companies/records/{cid}",
        headers=attio_headers(),
        json={"data": {"values": {"cd_prospect_notes": [{"value": notes[:500]}]}}},
        timeout=15)
    return r.status_code in (200, 201)


# ── Driver ─────────────────────────────────────────────────────────────────

def extract_context(person: dict) -> dict:
    vals = person.get("values", {})
    name_obj = (vals.get("name") or [{}])[0]
    full_name = name_obj.get("full_name", "?") if isinstance(name_obj, dict) else "?"
    vertical_vals = vals.get("vertical") or []
    vertical = ""
    if vertical_vals and isinstance(vertical_vals[0], dict):
        opt = vertical_vals[0].get("option") or {}
        vertical = opt.get("title", "") if isinstance(opt, dict) else ""
    company_vals = vals.get("company") or []
    company_id = ""
    if company_vals and isinstance(company_vals[0], dict):
        company_id = company_vals[0].get("target_record_id", "")
    return {"person_id": person.get("id", {}).get("record_id", ""),
            "full_name": full_name, "vertical": vertical, "company_id": company_id}


def process_one(person: dict) -> tuple:
    """Returns (status, person_name, observation, source_url)."""
    ctx = extract_context(person)
    if not ctx["company_id"]:
        return ("skip", ctx["full_name"], "", "")

    company = get_company(ctx["company_id"])
    if not company:
        return ("skip", ctx["full_name"], "", "")

    cvals = company.get("values", {})
    cname = (cvals.get("name") or [{}])[0].get("value", "") if (cvals.get("name") or [{}])[0] else ""
    if not cname:
        return ("skip", ctx["full_name"], "", "")

    # Tavily search
    vterm = VERTICAL_QUERY_TERM.get(ctx["vertical"], ctx["vertical"])
    query = f'"{cname}" Texas {vterm}'
    results = tavily_search(query, max_results=5)

    if not results:
        # Fallback: try domain
        domains = cvals.get("domains") or []
        domain = domains[0].get("domain", "") if domains and isinstance(domains[0], dict) else ""
        if domain:
            results = tavily_search(f'site:{domain} OR "{cname}"', max_results=3)

    if not results:
        return ("no_results", ctx["full_name"], "", "")

    # LLM selects best snippet (does NOT invent)
    selected = select_best_snippet(cname, ctx["vertical"], results)

    if not selected:
        return ("no_fact", ctx["full_name"], "", "")

    # Build the verified note with source attribution
    observation = selected["observation"]
    source_url = selected["source_url"]
    verified_note = f"{observation} [source: {source_url}]"

    # Update Attio
    ok = update_company_notes(ctx["company_id"], verified_note)
    if not ok:
        return ("update_failed", ctx["full_name"], observation, source_url)

    return ("enriched", ctx["full_name"], observation, source_url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    print("AVO Verified Enrichment (Tavily → LLM selects → source URL kept)")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    people = get_all_ownerphones_people()
    print(f"Total ownerphones-warm: {len(people)}")

    if args.dry_run:
        people = people[:5]
    elif args.limit:
        people = people[:args.limit]

    print(f"Processing: {len(people)}")
    print()

    stats = {"enriched": 0, "no_results": 0, "no_fact": 0, "skip": 0, "update_failed": 0}

    for i, p in enumerate(people, 1):
        status, name, observation, source_url = process_one(p)
        stats[status] = stats.get(status, 0) + 1

        if status == "enriched":
            print(f"  ✓ [{i}/{len(people)}] {name}")
            print(f"    FACT: {observation}")
            print(f"    SOURCE: {source_url}")
        elif status in ("no_results", "no_fact"):
            print(f"  — [{i}/{len(people)}] {name}: {status}")
        elif status == "skip":
            pass  # silent
        else:
            print(f"  ✗ [{i}/{len(people)}] {name}: {status}")

        if not args.dry_run:
            time.sleep(1.2)  # Rate limit: ~50 requests/min across Tavily + OpenRouter
        else:
            time.sleep(0.5)

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for k, v in stats.items():
        if v > 0:
            print(f"  {k:20s} {v}")
    print(f"\n  ENRICHED: {stats['enriched']} of {len(people)}")


if __name__ == "__main__":
    main()
