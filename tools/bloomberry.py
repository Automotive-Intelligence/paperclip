"""
tools/bloomberry.py - Bloomberry tech stack enrichment for prospect qualification.

Scores auto-dealership AI-readiness by analyzing their current tech stack:
  - Dealers on legacy DMS/CRM with NO AI = prime targets (pain exists, no solution)
  - Dealers with digital retailing + chat but no AI = digitally mature, ready to buy
  - Dealers ALREADY using AI tools = exclude per ICP (they're solved)
  - Dealers with almost nothing = too early, low maturity
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
import logging
from typing import Optional

from services.http_client import request_with_retry

BLOOMBERRY_BASE_URL = "https://api.revealera.com"


# ═══════════════════════════════════════════════════════════════════
# DEALERSHIP TECH STACK INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════
# Organized by what each category MEANS for our AI readiness pitch.
# Ryan sells: Free Assessment → $2.5K Audit → $7.5K Implementation
# Sweet spot = digitally mature dealer with NO AI in the stack.

# ── EXCLUDE: Already using dealer-specific AI ────────────────────
# Per Ryan's ICP: "EXCLUDE dealerships already using AI tools,
# AI phone systems, virtual assistants, chatbots"
# If ANY of these show up, the dealer is already solved.

_DEALER_AI_VENDORS = {
    # Dealer AI platforms
    "fullpath", "autoleadstar", "auto lead star",
    "conversica", "impel", "tekion",
    "puzzle auto", "orbee",
    # AI chat / virtual assistants
    "numa", "matador ai", "matador",
    "liveperson", "drift", "intercom",
    # General AI signals
    "openai", "anthropic", "chatgpt", "claude", "gemini",
    "copilot", "gpt", "langchain", "hugging face",
    # AI phone / voice
    "dialpad ai", "invoca", "marchex",
    "ai phone", "ai receptionist", "virtual assistant",
}

# ── LEGACY DMS: Pain signal — stuck on old systems ───────────────
# Dealers on these have BDC bottlenecks, slow lead response,
# manual processes. They FEEL the pain but haven't solved it.

_LEGACY_DMS = {
    "cdk global", "cdk", "reynolds and reynolds", "reynolds & reynolds",
    "dealertrack", "dealer track", "dealercenter", "dealer center",
    "dealer-fx", "dealerfx", "autosoft", "automate dms",
    "pbs systems", "quorum", "serti",
}

# ── DEALER CRM: Shows they invest in customer mgmt ──────────────
# Non-AI CRMs = they care about leads but handle them manually.
# This is the BDC pain Ryan talks about (4-hour response times).

_DEALER_CRM = {
    "vinsolutions", "vin solutions", "dealersocket", "dealer socket",
    "elead", "e-lead", "drivecentric", "drive centric",
    "autoraptor", "auto raptor", "selly automotive", "promax",
    "dominion dealer solutions", "dominion",
}

# ── DIGITAL RETAILING: Digitally mature signal ───────────────────
# These dealers are already investing in online buying experience.
# They GET digital — perfect audience for AI conversation.

_DIGITAL_RETAIL = {
    "roadster", "autofi", "auto fi", "gubagoo", "carnow", "car now",
    "rodo", "dealer inspire", "dealerinspire",
    "cox automotive", "modal", "accelerate auto",
    "digital motors", "motoinsight",
}

# ── INVENTORY MGMT: Operationally sophisticated ─────────────────
# Using vAuto or similar = data-driven decision makers.
# They'll understand AI ROI because they already measure everything.

_INVENTORY_TOOLS = {
    "vauto", "v-auto", "homenet", "home net",
    "stockwave", "provision", "conquest",
    "lotlinx", "carsoup", "firstlook",
    "max digital", "carfax for dealers",
}

# ── MARKETING / LEAD SOURCES: Spending on leads ─────────────────
# Dealers paying for leads from these platforms have acquisition cost
# pressure. AI reduces cost-per-lead and improves conversion.

_LEAD_PLATFORMS = {
    "cars.com", "autotrader", "auto trader", "cargurus", "car gurus",
    "truecar", "true car", "edmunds", "kbb", "kelley blue book",
    "dealer.com", "dealeron", "dealer on",
    "sincro", "shift digital", "reunited",
}

# ── CHAT / COMMUNICATION (non-AI): Manual chat = BDC pain ───────
# Human-staffed chat = they know chat matters but pay humans
# to do what AI could handle. Direct upgrade opportunity.

_MANUAL_CHAT = {
    "podium", "kenect", "callrevu", "call revu",
    "xtime", "autoloop", "auto loop", "mykaarna", "mykaarma",
    "textdrip", "zipwhip", "twilio",
    "webchat", "live chat", "olark",
}

# ── FIXED OPS / SERVICE: Service dept complexity ─────────────────
# Service scheduling + no-shows cost $200+/bay-hour.
# AI appointment confirmation is an easy win we can pitch.

_FIXED_OPS = {
    "dealer-fx", "dealerfx", "xtime", "spiffy",
    "tekmetric", "mitchell 1", "alldata",
    "fixed ops intel", "service lane",
    "time highway", "autopoint",
}

# ── GENERAL DIGITAL MATURITY: Website/analytics ─────────────────
# Basic digital infrastructure signals they're not a pen-and-paper lot.

_DIGITAL_INFRA = {
    "google analytics", "google tag manager", "google ads",
    "facebook pixel", "meta pixel", "hotjar", "hubspot",
    "salesforce", "mailchimp", "constant contact",
    "wordpress", "shopify", "wix", "squarespace",
    "cloudflare", "aws", "azure",
}


def _api_key() -> str:
    return (os.getenv("BLOOMBERRY_API_KEY") or "").strip()


def bloomberry_ready() -> bool:
    return bool(_api_key())


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
    }


def _match_vendors(vendor_names: list, keyword_set: set) -> list:
    """Return vendor names that match any keyword in the set."""
    matched = []
    for name in vendor_names:
        lower = name.lower()
        if any(kw in lower for kw in keyword_set):
            matched.append(name)
    return matched


def _score_dealer_prospect(vendor_names: list) -> dict:
    """
    Score a dealership prospect based on tech stack signals.

    Returns a dict with category matches and a composite verdict:
      - "prime"    = Legacy tech + digital maturity + NO AI → book the assessment
      - "strong"   = Some digital investment + no AI → worth pursuing
      - "exclude"  = Already has AI vendors → skip per ICP
      - "low"      = Minimal tech footprint → probably too early
    """
    ai_hits = _match_vendors(vendor_names, _DEALER_AI_VENDORS)
    dms_hits = _match_vendors(vendor_names, _LEGACY_DMS)
    crm_hits = _match_vendors(vendor_names, _DEALER_CRM)
    digital_retail_hits = _match_vendors(vendor_names, _DIGITAL_RETAIL)
    inventory_hits = _match_vendors(vendor_names, _INVENTORY_TOOLS)
    lead_hits = _match_vendors(vendor_names, _LEAD_PLATFORMS)
    chat_hits = _match_vendors(vendor_names, _MANUAL_CHAT)
    fixed_ops_hits = _match_vendors(vendor_names, _FIXED_OPS)
    infra_hits = _match_vendors(vendor_names, _DIGITAL_INFRA)

    # Count how many dealership-relevant categories have at least one hit
    category_depth = sum(1 for hits in [
        dms_hits, crm_hits, digital_retail_hits, inventory_hits,
        lead_hits, chat_hits, fixed_ops_hits, infra_hits,
    ] if hits)

    # ── Verdict logic ────────────────────────────────────────────
    if ai_hits:
        verdict = "exclude"
        reason = f"Already using AI: {', '.join(ai_hits)}"
    elif category_depth >= 4:
        verdict = "prime"
        reason = (
            f"Digitally mature ({category_depth} categories) with zero AI. "
            "Legacy stack = BDC pain + manual processes. Perfect for assessment."
        )
    elif category_depth >= 2:
        verdict = "strong"
        reason = (
            f"Some digital investment ({category_depth} categories), no AI. "
            "Worth pursuing — they understand tech but haven't made the AI leap."
        )
    elif category_depth >= 1:
        verdict = "possible"
        reason = (
            f"Minimal tech footprint ({category_depth} category). "
            "May need education before they're ready for AI conversation."
        )
    else:
        verdict = "low"
        reason = "No recognizable dealer tech detected. Likely too early or data unavailable."

    # ── Pain points we can reference in outreach ─────────────────
    pain_points = []
    if dms_hits and not ai_hits:
        pain_points.append(f"Running {dms_hits[0]} — likely manual BDC workflows, slow lead response")
    if crm_hits and not ai_hits:
        pain_points.append(f"Using {crm_hits[0]} CRM without AI — leads sitting in queue")
    if chat_hits:
        pain_points.append(f"Human-staffed chat ({chat_hits[0]}) — AI could handle 80% of these")
    if lead_hits:
        sources = ", ".join(lead_hits[:3])
        pain_points.append(f"Paying for leads from {sources} — AI improves conversion on existing spend")
    if fixed_ops_hits:
        pain_points.append("Service dept tools detected — AI appointment confirmation reduces no-shows")
    if inventory_hits and not ai_hits:
        pain_points.append(f"Data-driven inventory ({inventory_hits[0]}) but no AI — they measure ROI, pitch numbers")

    return {
        "verdict": verdict,
        "reason": reason,
        "category_depth": category_depth,
        "pain_points": pain_points,
        "matches": {
            "ai_vendors": ai_hits,
            "dms": dms_hits,
            "crm": crm_hits,
            "digital_retail": digital_retail_hits,
            "inventory": inventory_hits,
            "lead_sources": lead_hits,
            "chat_tools": chat_hits,
            "fixed_ops": fixed_ops_hits,
            "digital_infra": infra_hits,
        },
    }


def get_tech_stack(domain: str, category: Optional[str] = None) -> dict:
    """
    Look up the technology vendors used by a company domain.

    Returns enriched result with dealership-specific scoring.
    """
    if not bloomberry_ready():
        return {"domain": domain, "vendors": [], "error": "BLOOMBERRY_API_KEY not set"}

    clean_domain = domain.strip().lower().replace("https://", "").replace("http://", "").rstrip("/")
    if "/" in clean_domain:
        clean_domain = clean_domain.split("/")[0]

    params = {"api_key": _api_key(), "domain": clean_domain}
    if category:
        params["category"] = category

    resp = request_with_retry(
        provider="bloomberry",
        operation="get_tech_stack",
        method="GET",
        url=f"{BLOOMBERRY_BASE_URL}/enrichments/tech.json",
        headers=_headers(),
        params=params,
        timeout=15,
    )

    if not resp.ok:
        error_msg = resp.error.message if resp.error else f"HTTP {resp.status_code}"
        logging.warning(f"[Bloomberry] Tech stack lookup failed for {clean_domain}: {error_msg}")
        return {"domain": clean_domain, "vendors": [], "error": error_msg}

    data = resp.data or {}
    vendors = data.get("technologies") or data.get("vendors") or data.get("tech") or []

    # Normalize — API may return list of strings or list of dicts
    vendor_names = []
    for v in vendors:
        if isinstance(v, str):
            vendor_names.append(v)
        elif isinstance(v, dict):
            vendor_names.append(v.get("name") or v.get("vendor") or str(v))

    # Run dealership-specific scoring
    scoring = _score_dealer_prospect(vendor_names)

    result = {
        "domain": clean_domain,
        "vendors": vendor_names,
        "vendor_count": len(vendor_names),
        "verdict": scoring["verdict"],
        "reason": scoring["reason"],
        "category_depth": scoring["category_depth"],
        "pain_points": scoring["pain_points"],
        "matches": scoring["matches"],
        "error": None,
    }

    logging.info(
        f"[Bloomberry] {clean_domain}: {len(vendor_names)} vendors, "
        f"verdict={scoring['verdict']}, depth={scoring['category_depth']}"
    )
    return result


def enrich_prospect_tech(prospect: dict) -> dict:
    """
    Add tech stack intelligence to a prospect dict.

    Reads the 'website' or 'domain' field, runs Bloomberry lookup,
    and adds dealership scoring fields.
    """
    domain = (
        prospect.get("domain")
        or prospect.get("website")
        or ""
    ).strip()

    if not domain or not bloomberry_ready():
        return prospect

    tech = get_tech_stack(domain)

    updated = dict(prospect)
    updated["tech_vendor_count"] = tech["vendor_count"]
    updated["tech_verdict"] = tech["verdict"]
    updated["tech_reason"] = tech["reason"]
    updated["tech_category_depth"] = tech["category_depth"]
    updated["tech_pain_points"] = tech["pain_points"]
    updated["tech_matches"] = tech["matches"]
    updated["tech_stack"] = tech["vendors"]

    return updated


def enrich_prospects_tech(prospects: list) -> list:
    """
    Enrich a list of prospect dicts with tech stack data.
    Only enriches prospects that have a website/domain field.
    """
    if not bloomberry_ready():
        logging.info("[Bloomberry] BLOOMBERRY_API_KEY not set — skipping tech enrichment.")
        return prospects

    enriched = []
    looked_up = 0
    verdicts = {"prime": 0, "strong": 0, "possible": 0, "exclude": 0, "low": 0}

    for p in prospects:
        domain = (p.get("domain") or p.get("website") or "").strip()
        if not domain:
            enriched.append(p)
            continue

        try:
            result = enrich_prospect_tech(p)
            enriched.append(result)
            looked_up += 1
            v = result.get("tech_verdict", "low")
            verdicts[v] = verdicts.get(v, 0) + 1
        except Exception as e:
            logging.warning(f"[Bloomberry] Failed for '{p.get('business_name')}': {e}")
            enriched.append(p)

    logging.info(
        f"[Bloomberry] Tech enrichment complete: {looked_up} lookups | "
        f"prime={verdicts['prime']} strong={verdicts['strong']} "
        f"possible={verdicts['possible']} exclude={verdicts['exclude']} "
        f"low={verdicts['low']}"
    )
    return enriched
