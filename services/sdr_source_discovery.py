"""services/sdr_source_discovery.py -- automated NEW-prospect sourcing.

Michael, 2026-08-09: "FIND NEW PROSPECTS, THESE ARE OVER" -- the WD rebuild
demo list was dead (Vercel cleanup), and the SDR engine's own daily sweep
was proving it re-scans a fully-worked company list and finds nothing new.
The Sunday-night fix was manual: hand-research via web search, dedupe, load.
This is the repeatable version of that same fix, using the Google Places API
(New) key Michael provisioned 2026-08-11.

Scope discipline: this module ONLY sources and loads plain company records
(name + domain) -- exactly the same write shape as the manual Sunday load.
It never writes a defect, a contact, or a claim; the existing verification
gate (services/sdr_verification_gate.py) does that work, unchanged, on its
normal daily sweep. No new offer, no new pricing, no new template. Adding a
company here is strictly upstream of the gate that already exists.

commit=False (default) is a full dry-run: queries Places, computes what
WOULD be written, writes nothing.

NOT wired to a cron. Places API (New) Text Search has a real per-request
cost beyond the free tier -- cadence and batch size are an owner decision
about recurring spend, same class of decision as an ad budget. Fire it
on-demand via POST /admin/run-source-discovery until Michael sets a schedule.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)

_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# desk brand -> twenty runtime key. Sourcing today is WD-only: it feeds the
# rebuild-motion gate, which is WD's motion (see sdr_first_touch.py's
# brand_motion_mismatch gate). Extend when AvI/Book'd get their own motion.
_BRANDS: Dict[str, str] = {"wd": "callingdigital"}

# ICP segment -> a real, natural-language Places search phrase. smb_owner_general
# is deliberately excluded -- too generic to search meaningfully.
_SEGMENT_QUERIES: Dict[str, str] = {
    "med_spa_owner": "med spa",
    "pi_law_firm_partner": "personal injury law firm",
    "real_estate_team_lead": "real estate team",
    "custom_home_builder": "custom home builder",
}

# A small, explicit DFW-suburb rotation -- deliberately bounded, not
# unlimited. Add cities here as a conscious choice, not an auto-expansion.
DEFAULT_CITIES: List[str] = [
    "Frisco, Texas", "McKinney, Texas", "Southlake, Texas", "Plano, Texas",
    "Allen, Texas", "Prosper, Texas", "Flower Mound, Texas", "Denton, Texas",
]

_JUNK_DOMAIN_HOSTS = frozenset({
    "gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com",
})


def _places_key() -> str:
    key = (os.getenv("GOOGLE_PLACES_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY not set")
    return key


def _domain_host(url: str) -> str:
    h = (url or "").strip().lower()
    h = h.split("://", 1)[-1].split("/", 1)[0]
    return h[4:] if h.startswith("www.") else h


def search_places(query: str, *, limit: int = 8) -> List[dict]:
    """Real Places (New) Text Search. Returns [{name, domain, rating,
    review_count}], website-less results dropped (nothing to verify later)."""
    r = requests.post(
        _PLACES_URL, timeout=15,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": _places_key(),
            "X-Goog-FieldMask": "places.displayName,places.rating,"
                                "places.userRatingCount,places.websiteUri",
        },
        json={"textQuery": query, "maxResultCount": limit},
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("places", []):
        site = p.get("websiteUri") or ""
        domain = _domain_host(site)
        if not domain or domain in _JUNK_DOMAIN_HOSTS:
            continue
        out.append({
            "name": (p.get("displayName") or {}).get("text", "").strip(),
            "domain": domain,
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"),
        })
    return out


def _known_names_and_domains(runtime_key: str) -> Set[str]:
    """Every existing company name + domain in the brand's Twenty, lowercased,
    for dedup. Mirrors the manual check done 2026-08-09/11."""
    from tools.twenty import _headers, _workspace_config
    base, key = _workspace_config(runtime_key)
    h = _headers(key)
    known: Set[str] = set()
    cursor = None
    for _ in range(15):
        url = f"{base}/rest/companies?limit=60" + (f"&starting_after={cursor}" if cursor else "")
        r = requests.get(url, headers=h, timeout=20)
        r.raise_for_status()
        for c in (r.json().get("data") or {}).get("companies") or []:
            n = (c.get("name") or "").strip().lower()
            d = _domain_host(((c.get("domainName") or {}).get("primaryLinkUrl")) or "")
            if n:
                known.add(n)
            if d:
                known.add(d)
        pi = r.json().get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    return known


def _create_company(runtime_key: str, name: str, domain: str) -> Optional[str]:
    from tools.twenty import _headers, _workspace_config
    base, key = _workspace_config(runtime_key)
    r = requests.post(f"{base}/rest/companies", headers=_headers(key), timeout=20,
                      json={"name": name, "domainName": {"primaryLinkUrl": f"https://{domain}"}})
    if not r.ok:
        return None
    return ((r.json().get("data") or {}).get("createCompany") or {}).get("id")


def discover_new_companies(brand_key: str, *, commit: bool = False,
                           cities: Optional[List[str]] = None,
                           per_query_limit: int = 8) -> dict:
    """Query Places per (ICP segment, city), dedupe against the brand's
    existing Twenty companies, load only genuinely new ones. Writes nothing
    but plain company records (name + domain) -- the verification gate does
    all defect/contact/claim work, unchanged, on its own next sweep."""
    counts = {"queried": 0, "found": 0, "new": 0, "written": 0, "errors": 0}
    lines: List[str] = []

    runtime_key = _BRANDS.get(brand_key)
    if not runtime_key:
        return {**counts, "digest": f"- {brand_key}: no sourcing motion configured (WD-only today)"}

    try:
        known = _known_names_and_domains(runtime_key)
    except Exception as e:
        counts["errors"] += 1
        return {**counts, "digest": f"- {brand_key}: could not read existing companies: "
                f"{type(e).__name__}: {e} -- aborted (dedup must be verified before writing)"}

    seen_this_run: Set[str] = set()
    for segment, phrase in _SEGMENT_QUERIES.items():
        for city in (cities or DEFAULT_CITIES):
            counts["queried"] += 1
            query = f"{phrase} in {city}"
            try:
                results = search_places(query, limit=per_query_limit)
            except Exception as e:
                counts["errors"] += 1
                lines.append(f"- {segment} / {city}: search failed {type(e).__name__}: {e}")
                continue
            for r in results:
                counts["found"] += 1
                key_n, key_d = r["name"].lower(), r["domain"].lower()
                if key_n in known or key_d in known or key_d in seen_this_run:
                    continue
                counts["new"] += 1
                seen_this_run.add(key_d)
                if not commit:
                    lines.append(f"- {segment} / {city}: WOULD LOAD {r['name']} ({r['domain']}, "
                                 f"{r.get('rating')}★/{r.get('review_count')})")
                    continue
                cid = _create_company(runtime_key, r["name"], r["domain"])
                if cid:
                    counts["written"] += 1
                    lines.append(f"- {segment} / {city}: LOADED {r['name']} ({r['domain']}) -> {cid}")
                else:
                    counts["errors"] += 1
                    lines.append(f"- {segment} / {city}: write failed for {r['name']}")
    return {**counts, "digest": "\n".join(lines) or "(no new companies found)"}
