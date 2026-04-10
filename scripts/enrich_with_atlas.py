# AVO — AI Business Operating System
# Atlas-style Tavily + Claude enrichment for OwnerPhones contacts
# Replaces generic cd_prospect_notes with real, current, web-sourced personalization
# Salesdroid — April 2026

"""Enrich the 197 OwnerPhones contacts with Tavily web research + Claude summarization.

For each contact:
  1. Pull the linked Company from Attio
  2. Run a Tavily search: "<company> <city> Texas <vertical> reviews news 2025 2026"
  3. Send the search results to Claude with a prompt that asks for ONE
     specific, current observation about the business that could open
     a cold email
  4. Update the Company's cd_prospect_notes with the result

Cost estimate: ~$0.01 per contact via Claude Haiku → ~$2 total for 197.
Time estimate: ~3-5 min for the full run with rate limiting.

Idempotent — re-running just refreshes the notes with the latest Tavily data.

Usage:
    python scripts/enrich_with_atlas.py --dry-run     # Process 3, no writes
    python scripts/enrich_with_atlas.py --limit 10    # Process first 10, live writes
    python scripts/enrich_with_atlas.py               # Full live run on all 197
"""

import argparse
import os
import sys
import time
from typing import Optional

import requests

ATTIO_BASE = "https://api.attio.com/v2"


def attio_token() -> str:
    t = (os.getenv("ATTIO_API_KEY") or "").strip()
    if not t:
        print("ERROR: ATTIO_API_KEY not set")
        sys.exit(1)
    return t


def attio_headers() -> dict:
    return {
        "Authorization": f"Bearer {attio_token()}",
        "Content-Type": "application/json",
    }


def tavily_token() -> str:
    t = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not t:
        print("ERROR: TAVILY_API_KEY not set")
        sys.exit(1)
    return t


def openrouter_token() -> str:
    t = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not t:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)
    return t


# ── Tavily ─────────────────────────────────────────────────────────────────

def tavily_search(query: str, max_results: int = 4) -> str:
    """Run a Tavily search via REST API and return concatenated result content."""
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": tavily_token(),
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return f"<tavily error {r.status_code}>"
        results = r.json().get("results", [])
        if not results:
            return ""
        return "\n\n".join(
            f"[{r.get('title', '')}] {r.get('content', '')[:400]}"
            for r in results
        )
    except Exception as e:
        return f"<tavily error: {e}>"


# ── Claude ─────────────────────────────────────────────────────────────────

def claude_summarize(company_name: str, city: str, vertical: str, search_results: str) -> str:
    """Send search results to OpenRouter (DeepSeek) for a one-line cold-email opener."""
    if not search_results or search_results.startswith("<tavily error"):
        return ""

    prompt = f"""I'm writing a cold email TO the owner of {company_name}, a Texas {vertical} business. I need a personalized opening sentence that proves I researched them.

Recent web search results about {company_name}:
{search_results[:2500]}

Your task: Write ONE sentence (15-25 words) that I can use as the OPENING of my email. The sentence must reference a SPECIFIC fact from the search results above — something current and verifiable like: a recent award, expansion, hiring, news mention, milestone, partner achievement, case result, location opening, or other notable detail.

CRITICAL RULES — read carefully:
- The sentence is FROM ME (the cold emailer) TO THEM (the recipient).
- Do NOT write in the voice of the recipient's company. Do NOT say "we help" or "we offer" — that's THEIR website copy, not what I'm sending them.
- Do NOT pitch anything. The sentence is observational only — it tells them I noticed something specific.
- Reference a real SPECIFIC fact from the search results. No vague compliments.
- ONE sentence only. No second sentence. No "Note:". No alternatives. No parentheticals.
- No surrounding quotes.
- If the search results contain NO specific facts (just generic boilerplate), output exactly: NO_SPECIFIC_DATA

Good example: "Saw Springer & Lyle's recent recognition for 30 years of personal injury trial work in Denton — that's a long track record."

Bad example: "We help builders acquire land fast." (this is in THEIR voice, not mine)
Bad example: "Impressive work!" (no specific fact)
Bad example: "Your firm shows commitment to teamwork." (vague)

Output: just the sentence. Nothing else."""

    # Try up to 3 times with backoff for rate limits
    for attempt in range(3):
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_token()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek/deepseek-chat",
                    "max_tokens": 120,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if r.status_code == 429:
                # Rate limited — back off and retry
                time.sleep(2 + attempt * 3)
                continue
            if r.status_code != 200:
                return f"<llm error {r.status_code}: {r.text[:150]}>"
            data = r.json()
            choices = data.get("choices", [])
            if not choices or not isinstance(choices[0], dict):
                return ""
            text = (choices[0].get("message", {}).get("content") or "").strip()

            # Clean LLM artifacts
            text = _clean_llm_output(text)

            if not text or "NO_SPECIFIC_DATA" in text:
                return ""
            return text
        except Exception as e:
            if attempt == 2:
                return f"<llm exception: {e}>"
            time.sleep(2)
    return ""


def _clean_llm_output(text: str) -> str:
    """Strip common LLM verbosity: parentheticals, alternatives, quotes, notes."""
    # Strip wrapping quotes
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    # Cut at common LLM meta-commentary markers
    cutoff_markers = [
        "\n\n(Note:",
        "\n(Note:",
        "\n\n(Alternative",
        "\n(Alternative",
        "\n\nNote:",
        "\nNote:",
        "\n\nP.S.",
        "\nP.S.",
        "\n\n(Tighter",
        "\n(Tighter",
        "\n\n(If",
        "\n(If",
    ]
    for marker in cutoff_markers:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx].strip()

    # Take only the first sentence if multiple sentences came back.
    # Use a regex that ignores common abbreviations like "C.J.", "Dr.", "Mr.", "Inc.", "LLP", etc.
    import re
    # Match sentence end: period/question/exclamation followed by space + capital letter,
    # but NOT preceded by single capital letter (initials) or common abbrevs
    sentence_end_pattern = re.compile(r'(?<![A-Z])(?<!Dr)(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Inc)(?<!LLP)(?<!Ltd)(?<!Co)(?<!St)([.!?])\s+(?=[A-Z])')
    match = sentence_end_pattern.search(text)
    if match:
        # Only cut if there's substantial content after the first sentence
        first_sentence = text[:match.end(1)].strip()
        remaining = text[match.end():].strip()
        if len(remaining) > 20 and not remaining.startswith("("):
            text = first_sentence

    # Strip wrapping quotes again after cleaning
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    return text


# ── Attio data layer ───────────────────────────────────────────────────────

def get_all_ownerphones_people() -> list:
    """Pull all People with pipeline_stage = ownerphones-warm."""
    r = requests.post(
        f"{ATTIO_BASE}/objects/people/records/query",
        headers=attio_headers(),
        json={"filter": {"pipeline_stage": {"$eq": "ownerphones-warm"}}, "limit": 500},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"ERROR fetching people: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    return r.json().get("data", [])


def get_company(company_id: str) -> Optional[dict]:
    r = requests.get(
        f"{ATTIO_BASE}/objects/companies/records/{company_id}",
        headers=attio_headers(),
        timeout=15,
    )
    if r.status_code == 200:
        return r.json().get("data")
    return None


def update_company_prospect_notes(company_id: str, new_notes: str) -> bool:
    payload = {
        "data": {
            "values": {
                "cd_prospect_notes": [{"value": new_notes[:500]}],
            }
        }
    }
    r = requests.patch(
        f"{ATTIO_BASE}/objects/companies/records/{company_id}",
        headers=attio_headers(),
        json=payload,
        timeout=15,
    )
    return r.status_code in (200, 201)


# ── Driver ─────────────────────────────────────────────────────────────────

def extract_person_context(person: dict) -> dict:
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

    return {
        "person_id": person.get("id", {}).get("record_id", ""),
        "full_name": full_name,
        "vertical": vertical,
        "company_id": company_id,
    }


def extract_company_context(company: dict) -> dict:
    vals = company.get("values", {})
    name = (vals.get("name") or [{}])[0]
    company_name = name.get("value", "") if isinstance(name, dict) else ""

    domains = vals.get("domains") or []
    domain = ""
    if domains and isinstance(domains[0], dict):
        domain = domains[0].get("domain", "")

    # Pull city from any description with locality info — fall back to empty
    desc_obj = (vals.get("description") or [{}])[0]
    desc = desc_obj.get("value", "") if isinstance(desc_obj, dict) else ""

    return {
        "name": company_name,
        "domain": domain,
        "description": desc,
    }


VERTICAL_QUERY_TERM = {
    "med-spa": "med spa",
    "pi-law": "personal injury attorney",
    "real-estate": "real estate",
    "home-builder": "custom home builder",
}


VERTICAL_HOOK = {
    "med-spa": (
        "Most med spas in Texas miss 30%+ of after-hours inquiries — Sophie can answer "
        "every one without changing how the front desk runs."
    ),
    "pi-law": (
        "Most PI firms in Texas lose new clients to voicemail after 5pm — AI intake "
        "captures every case without changing how the firm practices."
    ),
    "real-estate": (
        "Most Texas agents lose 1-2 deals a month to slow lead response — an AI front "
        "desk answers instantly while you're showing homes."
    ),
    "home-builder": (
        "Most Texas custom builders are referral-dependent and inbound leads slip "
        "through the cracks while they're at jobsites — AI can qualify them."
    ),
}


def build_enriched_note(observation: str, vertical: str) -> str:
    """Combine the Tavily-sourced observation with the vertical hook."""
    hook = VERTICAL_HOOK.get(vertical, "")
    if observation:
        return f"{observation} {hook}".strip()
    return hook


def process_one(person: dict) -> tuple[str, str, str]:
    """Returns (status, person_name, observation)."""
    ctx = extract_person_context(person)
    if not ctx["company_id"]:
        return ("skip_no_company", ctx["full_name"], "")

    company = get_company(ctx["company_id"])
    if not company:
        return ("skip_no_company_record", ctx["full_name"], "")

    ccx = extract_company_context(company)
    if not ccx["name"]:
        return ("skip_no_company_name", ctx["full_name"], "")

    # Build the Tavily query — use friendly vertical term for better results
    friendly_vertical = VERTICAL_QUERY_TERM.get(ctx["vertical"], ctx["vertical"])
    query = f'"{ccx["name"]}" Texas {friendly_vertical}'
    search_results = tavily_search(query, max_results=4)

    if not search_results or search_results.startswith("<tavily error"):
        # Fallback: try just the domain
        if ccx["domain"]:
            query2 = f'site:{ccx["domain"]} OR "{ccx["name"]}" recent'
            search_results = tavily_search(query2, max_results=3)

    if not search_results:
        return ("no_search_results", ctx["full_name"], "")

    # Send to Claude
    observation = claude_summarize(ccx["name"], "", ctx["vertical"], search_results)

    if not observation or observation.startswith("<claude"):
        return ("no_observation", ctx["full_name"], observation)

    # Update Attio
    new_notes = build_enriched_note(observation, ctx["vertical"])
    ok = update_company_prospect_notes(ctx["company_id"], new_notes)
    if not ok:
        return ("update_failed", ctx["full_name"], observation)

    return ("enriched", ctx["full_name"], observation)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Process 3 contacts, no writes")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N contacts")
    args = parser.parse_args()

    print("AVO Atlas-style enrichment for OwnerPhones contacts")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    if args.limit:
        print(f"Limit: {args.limit}")
    print()

    people = get_all_ownerphones_people()
    print(f"Total ownerphones-warm contacts: {len(people)}")

    if args.dry_run:
        people = people[:3]
    elif args.limit:
        people = people[:args.limit]

    print(f"Processing: {len(people)}")
    print()

    stats = {
        "enriched": 0,
        "skip_no_company": 0,
        "skip_no_company_record": 0,
        "skip_no_company_name": 0,
        "no_search_results": 0,
        "no_observation": 0,
        "update_failed": 0,
    }

    for i, p in enumerate(people, 1):
        if args.dry_run:
            ctx = extract_person_context(p)
            company = get_company(ctx["company_id"]) if ctx["company_id"] else None
            ccx = extract_company_context(company) if company else {"name": "?"}
            friendly_vertical = VERTICAL_QUERY_TERM.get(ctx["vertical"], ctx["vertical"])
            query = f'"{ccx["name"]}" Texas {friendly_vertical}'
            print(f"  [{i}] {ctx['full_name']} @ {ccx['name']} ({ctx['vertical']})")
            print(f"      query: {query}")
            search_results = tavily_search(query, max_results=4)
            print(f"      tavily returned: {len(search_results)} chars")
            if search_results:
                obs = claude_summarize(ccx["name"], "", ctx["vertical"], search_results)
                print(f"      observation: {obs}")
                print(f"      enriched note: {build_enriched_note(obs, ctx['vertical'])[:200]}")
            print()
            time.sleep(0.5)
            continue

        status, name, observation = process_one(p)
        stats[status] = stats.get(status, 0) + 1

        if status == "enriched":
            print(f"  ✓ [{i}/{len(people)}] {name}: {observation[:120]}")
        else:
            print(f"  ⚠ [{i}/{len(people)}] {name}: {status} {observation[:80] if observation else ''}")

        # Rate limit: be polite to both Tavily and OpenRouter (DeepSeek free tier
        # rate-limits aggressively). 1 req/sec keeps us comfortably under any cap.
        time.sleep(1.0)

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for k, v in stats.items():
        if v > 0:
            print(f"  {k:25s} {v}")
    print()
    total_enriched = stats["enriched"]
    print(f"  TOTAL ENRICHED: {total_enriched} of {len(people)}")


if __name__ == "__main__":
    main()
