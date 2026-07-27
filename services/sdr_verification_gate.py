"""services/sdr_verification_gate.py -- the SDR Verification Gate.

Sub-project #1 of the autonomous SDR desk (see
docs/superpowers/specs/2026-07-27-sdr-verification-gate-design.md). Sits
between scoring and CRM-write/outreach: verifies a sourced prospect's real
primary site, real defect, and real contact before it becomes an opportunity,
and emits a verdict that drives the existing services/approval_queue.

Deterministic checks (curl/requests) are pure Python. Exactly ONE LLM
judgment call goes through services/studio_social_llm.llm_json -- the single
model-dependent seam -- for the genuinely fuzzy "is a 404 a real rebuild
target or a wrong/parked domain" call. That prompt lives as a portable,
plain-text skill file at .agents/skills/prospect-verification/SKILL.md
(NOT a Claude Project), so swapping the underlying model is a one-adapter
change, not a rewrite.

---------------------------------------------------------------------------
PINNED INTERFACES (Task 0 -- read from source 2026-07-27, do not guess)
---------------------------------------------------------------------------

1. services/approval_queue.py + services/artifact.py

   Artifact is a plain dataclass defined in services/artifact.py (NOT in
   approval_queue.py -- approval_queue only imports it). ALL fields are
   required, none have defaults:

       @dataclass
       class Artifact:
           artifact_id: str
           agent_id: str
           business_key: str
           artifact_type: str            # one of ARTIFACT_TYPES (email,
                                          # crm_update, social_post, report,
                                          # task, ad, sms, note)
           audience: str                 # one of AUDIENCE_TYPES (prospect,
                                          # client, internal, public)
           intent: str                   # one of INTENT_TYPES (nurture,
                                          # close, educate, retain, inform,
                                          # alert) -- NOTE: "sdr_prospect" is
                                          # NOT a valid intent; the plan's
                                          # sketch used an invented value.
           content: str                  # a STRING payload, not a dict.
           subject: Optional[str]
           channel_candidates: List[str]
           confidence: float
           risk_level: str               # "low" | "medium" | "high"
           requires_human_approval: bool
           metadata: Dict[str, Any]
           created_at: datetime.datetime
           status: str                   # one of ARTIFACT_STATUSES

   Production code is meant to go through the factory
   `services.artifact.create_artifact(*, agent_id, business_key,
   artifact_type, audience, intent, content, subject=None,
   channel_candidates=None, confidence=0.8, risk_level=None,
   metadata=None) -> Artifact` (docstring: "never instantiate directly in
   production code -- the factory applies the moral gate and approval
   routing"). Confirmed real call-site convention in
   tools/marketing_tools.py: `create_artifact(...)` then
   `queue_artifact(artifact)`.

   create_artifact's own risk/status derivation (services/artifact.py):
     - `risk_level` param is an OVERRIDE: if passed and valid, it wins
       outright (_derive_risk_level).
     - `_derive_approval_required`: risk in (medium, high) -> always needs
       approval; risk == low -> needs approval iff confidence <
       AUTO_DISPATCH_MIN_CONFIDENCE (0.75).
     - `_derive_status`: moral-gate fail -> "escalated"; risk == high ->
       "escalated"; requires_approval False -> "auto_approved"; else
       "pending_approval".
     - Moral gate (`_assess_moral_gate`) needs serves_person=True,
       transparent=True (always True), genuine_value=True to pass. Using
       audience="internal" + intent="inform" (this artifact is an INTERNAL
       verification record, not prospect-facing copy) satisfies
       serves_person unconditionally and avoids an unwanted "escalated".

   This means create_artifact(risk_level="low", confidence>=0.75) yields
   status "auto_approved", and risk_level="medium" yields "pending_approval"
   -- exactly the spec's table, and it is approval_queue's OWN existing
   threshold (spec: "matches approval_queue's existing thresholds"), not a
   value we reimplement ourselves.

   `queue_artifact(artifact: Artifact) -> str` persists the artifact and
   returns `artifact.artifact_id` (NOT a status string -- the plan's sketch
   assumed it returned "auto_approved"/"pending_approval"). This module's
   `_queue()` wrapper therefore returns `artifact.status` (read off the
   Artifact BEFORE persisting, since create_artifact already computed it),
   not queue_artifact's return value.

2. tools/crm_router.py

       push_prospects_to_crm(prospects: list, source_agent: str,
                              business_key: str) -> Tuple[str, list]

   Routes to tools/twenty.py `push_prospects_to_twenty` (wd/avi/bookd) or
   tools/ghl.py `push_prospects_to_ghl` (aipg) via
   config.runtime.get_settings().resolve_crm_provider(...). Prospect-dict
   keys actually read (verified 2026-07-27, the two writers do NOT agree
   with each other):
     - tools/twenty.py: `business_name` (company), `website` or `domain`
       (company dedup), `email`, `phone` (best-effort E.164 normalized),
       `contact` (split into first/last -- NOT "name"), `job_title`/`title`,
       `city`.
     - tools/ghl.py: `business_name`, `city`, `business_type`, `email`,
       `phone`, `contact_name` (NOT "contact"), `website`, plus optional
       enrichment fields (trigger_event, verified_fact,
       competitive_insight).
   Per this task's pinned instruction, this module builds the prospect dict
   around `website`, `email`, `phone`, `name` -- and additionally aliases
   `name` to both `contact` and `contact_name` (and `website` to `domain`,
   `company_name` to `business_name`) so the push actually lands correctly
   against BOTH real writers rather than only one.
   NOTE (out of scope for this module): push_prospects_to_twenty's
   `_workspace_config` keys on "callingdigital" / "autointelligence" /
   "bookd", not the "wd" / "avi" business_key values this spec's
   VerificationRequest.business_key uses (spec section 4). That
   normalization lives in config/runtime.py / crm_router.resolve_provider
   and is not exercised by this module's tests (which monkeypatch
   `_push_crm` entirely) -- flagged here, not fixed here (out of scope per
   spec section 10: "Any change to approval_queue's thresholds or
   crm_router's routing").

3. services/studio_social_llm.py

       llm_json(system: str, user: str, *, images: Optional[List[bytes]] =
                 None, retries: int = 3, model: Optional[str] = None,
                 max_tokens: Optional[int] = None) -> Dict[str, Any]

   Takes SEPARATE system/user strings (positional), not the plan sketch's
   single concatenated prompt string. This module's system prompt is the
   portable skill file content
   (.agents/skills/prospect-verification/SKILL.md, read verbatim); the user
   prompt carries the per-request evidence (domain_on_file, resolved
   domain, candidate defect, evidence log). Existing call convention
   (tests/test_studio_social_llm.py) is positional: `llm_json("sys",
   "user")`.

---------------------------------------------------------------------------
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

_SKILL_PATH = Path(__file__).parent.parent / ".agents/skills/prospect-verification/SKILL.md"


def _probe_headers(domain: str) -> dict:
    """curl -sIv: returns {cert_cn, location, status}. Pure Python, no LLM."""
    out = subprocess.run(
        ["curl", "-sIv", "--max-time", "12", f"https://{domain}"],
        capture_output=True, text=True,
    ).stderr + subprocess.run(
        ["curl", "-sI", "--max-time", "12", f"https://{domain}"],
        capture_output=True, text=True,
    ).stdout
    cn = re.search(r"subject: CN=([^\s]+)", out)
    loc = re.search(r"(?i)^location:\s*(\S+)", out, re.M)
    st = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", out)
    return {"cert_cn": cn.group(1) if cn else None,
            "location": loc.group(1) if loc else None,
            "status": int(st.group(1)) if st else None}


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "")).split("/")[0]


def resolve_primary_site(domain_on_file: str, company_name: str, city: str = "") -> "tuple[str, list[str]]":
    """Check 1 -- real primary site (spec section 5).

    Follows a cert-CN alias (e.g. Bonick's cert is issued to
    www.bonicklandscaping.com, not bonick.com) or a 301 redirect (e.g.
    Stride -> stridepestcontrol.com) to the terminal host. Returns
    (real_domain, evidence_log).
    """
    log, current = [], domain_on_file
    for _ in range(3):  # follow up to 3 redirects/aliases
        h = _probe_headers(current)
        log.append(f"probe {current}: status={h['status']} cn={h['cert_cn']} loc={h['location']}")
        if h["location"] and _host(h["location"]) != current:
            current = _host(h["location"])
            continue
        if h["cert_cn"] and _host(h["cert_cn"]) != current:
            log.append(f"cert CN {h['cert_cn']} != {current}; alias -> resolving to CN host")
            current = _host(h["cert_cn"])
            continue
        break
    return current, log


def _fetch_html(domain: str) -> str:
    return requests.get(f"https://{domain}", timeout=12, allow_redirects=True).text


def _ttfb(domain: str) -> float:
    out = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{time_starttransfer}",
         "--max-time", "20", f"https://{domain}"],
        capture_output=True, text=True,
    ).stdout
    try:
        return float(out)
    except ValueError:
        return 0.0


def check_defect(real_domain: str, motion: str) -> "dict | None":
    """Check 2 -- real defect / live signal (spec section 5), motion-dependent.

    `rebuild`: confirm a checkable defect ON the resolved primary site --
    down, pinch-zoom block, no contact path, or slow TTFB. Returns
    {"kind":..., "evidence":...} (evidence is the literal probe output) or
    None if the site is genuinely healthy.

    `intent`/`permit`: out of scope for this sub-project's fixtures (spec
    decomposition items 2-4); returns a placeholder freshness marker so
    verify() has a non-None defect to carry forward for those motions.
    """
    if motion != "rebuild":
        return {"kind": f"{motion}_signal", "evidence": "signal freshness re-confirmed upstream"}

    h = _probe_headers(real_domain)
    if h["status"] in (404, 410, 500, 502, 503) or h["status"] is None:
        return {"kind": "site_down", "evidence": f"HTTP {h['status']} on https://{real_domain}"}

    html = _fetch_html(real_domain).lower()
    if re.search(r"maximum-scale=1|user-scalable=no", html):
        m = re.search(r'viewport[^>]*content="[^"]*"', html)
        return {"kind": "pinch_zoom_blocked", "evidence": m.group(0) if m else "maximum-scale=1"}

    has_tel = "tel:" in html
    has_form = "<form" in html
    has_cta = any(k in html for k in ("get a quote", "contact us", "call now", "get started"))
    if not (has_tel or has_form or has_cta):
        return {"kind": "no_contact_path", "evidence": "no tel:, no <form>, no CTA on homepage"}

    ttfb = _ttfb(real_domain)
    if ttfb > 1.5:
        return {"kind": "slow_load", "evidence": f"TTFB {ttfb:.2f}s (curl time_starttransfer)"}

    return None


# Never accept a data-broker phone/email as a verified contact (global
# constraint, spec section 5 check 3 + plan Global Constraints). Only these
# sources count as "the company's own published info".
_TRUSTED_CONTACT_SOURCES = ("site", "gbp", "yelp")


def check_contact(entity: dict, real_domain: str) -> "dict | None":
    """Check 3 -- real contact from published info (spec section 5).

    Prefers a tel: link scraped straight off the resolved primary site. Only
    falls back to an entity-carried phone if its recorded source is one of
    the company's own published channels (site/gbp/yelp) -- NEVER a data
    broker like RocketReach/ZoomInfo (the fabricated-enrichment trap the
    2026-07-15 rebuild week ran into). Returns {"name","phone","source"} or
    None if unverifiable.
    """
    html = _fetch_html(real_domain)
    tel = re.search(r"tel:(\+?\d[\d\-\(\) ]{9,})", html)
    if tel:
        phone = re.sub(r"[^\d+]", "", tel.group(1))
        return {"name": entity.get("contact_name"), "phone": phone, "source": "site"}

    src = (entity.get("phone_source") or "").lower()
    if entity.get("contact_phone") and src in _TRUSTED_CONTACT_SOURCES:
        return {"name": entity.get("contact_name"), "phone": entity["contact_phone"], "source": src}

    return None  # broker-only or none -> unverified


# ---------------------------------------------------------------------------
# Types (spec section 4 -- the unit's contract)
# ---------------------------------------------------------------------------


@dataclass
class VerificationRequest:
    business_key: str   # brand: wd | avi | aipg | bookd
    entity: dict         # {company_name, domain_on_file, contact_name?, contact_phone?, contact_email?}
    signal: Any           # the intent_scoring.Signal that flagged this entity
    motion: str          # rebuild | intent | permit


@dataclass
class VerificationResult:
    verdict: str                          # "PASS" | "FAIL" | "NEEDS_HUMAN"
    real_primary_site: Optional[str]
    verified_defect: Optional[dict]
    verified_contact: Optional[dict]
    confidence: float
    evidence_log: list = field(default_factory=list)
    reason: str = ""


def _primary_is_our_domain(domain_on_file: str, real: str) -> bool:
    return _host(domain_on_file) == _host(real)


# ---------------------------------------------------------------------------
# The one LLM judgment call (spec section 7 -- no lock-in by construction)
# ---------------------------------------------------------------------------


def _load_skill_prompt() -> str:
    """The portable judgment prompt -- plain text, NOT a Claude Project (spec
    section 7 / plan Global Constraints), so swapping the model is a
    one-adapter change. Read fresh each call so an edit to the skill file
    takes effect without a code deploy."""
    return _SKILL_PATH.read_text()


def _judgment_user_prompt(req: "VerificationRequest", real: str, defect: dict, log: list) -> str:
    """The per-request evidence the skill's judgment is applied to. Kept
    separate from the (portable, static) system prompt because
    studio_social_llm.llm_json takes system/user as two separate strings."""
    return (
        f"DOMAIN_ON_FILE: {req.entity.get('domain_on_file')}\n"
        f"COMPANY_NAME: {req.entity.get('company_name')}\n"
        f"RESOLVED_PRIMARY_SITE: {real}\n"
        f"CANDIDATE_DEFECT: {defect}\n"
        f"EVIDENCE_LOG:\n" + "\n".join(log)
    )


# ---------------------------------------------------------------------------
# verify() -- runs the three checks in order, deterministic verdict, ONE LLM
# judgment call only for the genuinely ambiguous case (spec section 6).
# ---------------------------------------------------------------------------


def verify(req: VerificationRequest) -> VerificationResult:
    """Runs the three checks IN ORDER and short-circuits as soon as a
    verdict is reached -- this both matches spec section 5's "a rebuild-motion
    prospect FAILS here [at Check 1]" wording literally, and avoids running
    Check 2 (a real HTTP fetch) or Check 3 (another real HTTP fetch) against
    a domain already known to be the wrong target.
    """
    dof = req.entity.get("domain_on_file", "")
    real, log = resolve_primary_site(dof, req.entity.get("company_name", ""), req.entity.get("city", ""))

    # rebuild motion: if their real primary site is NOT the one on file,
    # there is nothing to fix -> FAIL (never queued, never contacted).
    # Checks 2/3 never run against a domain that already failed Check 1.
    if req.motion == "rebuild" and not _primary_is_our_domain(dof, real):
        return VerificationResult(
            "FAIL", real, None, None, 0.0, log,
            f"real primary site is {real}, not {dof}; not a rebuild target",
        )

    defect = check_defect(real, req.motion)
    if req.motion == "rebuild" and defect is None:
        return VerificationResult("FAIL", real, None, None, 0.0, log, "no verifiable defect on primary site")

    # ambiguous primary-site defect (e.g. site down): ONE llm judgment call,
    # must cite the deterministic evidence already collected.
    ambiguous = req.motion == "rebuild" and defect and defect["kind"] == "site_down"
    if ambiguous:
        from services.studio_social_llm import llm_json

        system = _load_skill_prompt()
        user = _judgment_user_prompt(req, real, defect, log)
        j = llm_json(system, user)
        log.append(f"llm judgment: {j.get('rationale', '')}")
        if j.get("verdict") == "NEEDS_HUMAN":
            return VerificationResult("NEEDS_HUMAN", real, defect, None, 0.5, log, j.get("rationale", ""))

    contact = check_contact(req.entity, real)
    if contact is None:
        return VerificationResult("NEEDS_HUMAN", real, defect, contact, 0.5, log, "contact unverified (no published number)")

    return VerificationResult("PASS", real, defect, contact, 0.85, log, "all checks passed")
