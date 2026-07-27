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

4. Check 1 corroboration scope (spec section 5) -- fix-round-1 note

   Cert-CN alias + 301 redirect + <link rel="canonical"> resolution are
   ALL implemented in `resolve_primary_site()`. The web-search / Google
   Business Profile corroboration step spec section 5 also lists is
   DEFERRED to the enrichment sub-project (#3 in the spec's Decomposition)
   by controller ruling -- it is NOT stubbed or fabricated here.
   `company_name`/`city` remain in `resolve_primary_site`'s signature for
   that future use; they are not read by the function today.

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
    """curl -sIv: returns {cert_cn, location, status, cert_error,
    cert_error_detail}. Pure Python, no LLM."""
    verbose = subprocess.run(
        ["curl", "-sIv", "--max-time", "12", f"https://{domain}"],
        capture_output=True, text=True,
    )
    plain = subprocess.run(
        ["curl", "-sI", "--max-time", "12", f"https://{domain}"],
        capture_output=True, text=True,
    )
    out = verbose.stderr + plain.stdout
    cn = re.search(r"subject: CN=([^\s]+)", out)
    loc = re.search(r"(?i)^location:\s*(\S+)", out, re.M)
    st = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", out)
    # A TLS cert failure aborts the handshake before curl ever gets an HTTP
    # status line, so `status` is None in this case too -- the caller
    # (check_defect) checks cert_error FIRST to surface this more specific
    # diagnosis instead of burying it in the generic "site_down" bucket.
    cert_m = re.search(r"^.*(?:SSL certificate problem|certificate verify failed).*$", verbose.stderr, re.I | re.M)
    return {
        "cert_cn": cn.group(1) if cn else None,
        "location": loc.group(1) if loc else None,
        "status": int(st.group(1)) if st else None,
        "cert_error": cert_m is not None,
        "cert_error_detail": cert_m.group(0).strip() if cert_m else None,
    }


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "")).split("/")[0]


def _canonical_host(html: str) -> "str | None":
    """Extract the host from a <link rel="canonical" href="..."> tag, if the
    page declares one. Checks `rel` and `href` independently within each
    <link> tag (rather than assuming a fixed attribute order), so
    `<link href="..." rel="canonical">` matches just as well as
    `<link rel="canonical" href="...">`."""
    for tag in re.findall(r"<link\b[^>]*>", html or "", re.I):
        if re.search(r'rel=["\']canonical["\']', tag, re.I):
            href_m = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
            if href_m:
                return _host(href_m.group(1))
    return None


def resolve_primary_site(domain_on_file: str, company_name: str, city: str = "") -> "tuple[str, list[str]]":
    """Check 1 -- real primary site (spec section 5).

    Follows a cert-CN alias (e.g. Bonick's cert is issued to
    www.bonicklandscaping.com, not bonick.com), a 301 redirect (e.g.
    Stride -> stridepestcontrol.com), and finally a <link rel="canonical">
    tag on the resolved page (if the page declares a different real host),
    to the terminal host. Returns (real_domain, evidence_log).

    Spec section 5 Check 1 also lists a web-search / Google Business
    Profile corroboration step ("web-search '{company_name} {city}' and
    read the GBP listing for the site they actually list"). That step is
    DEFERRED to the enrichment sub-project (#3 in the spec's Decomposition)
    by controller ruling -- it is NOT implemented or stubbed here.
    `company_name`/`city` are accepted and kept in this signature for that
    future use, but are not read by this function today.
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

    # Canonical link corroboration: the resolved page may itself declare a
    # different canonical host. A fetch failure here must never break Check
    # 1's cert/redirect resolution -- it just forfeits this one extra
    # corroboration signal and carries on with what cert/redirect already
    # found.
    try:
        canon_host = _canonical_host(_fetch_html(current))
    except Exception as exc:
        canon_host = None
        log.append(f"canonical fetch failed for {current}: {exc}")
    if canon_host and canon_host != current:
        log.append(f"canonical link on {current} points to {canon_host}; adopting canonical host")
        current = canon_host

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
    a TLS cert problem, HTTP down, pinch-zoom block, no contact path, or
    slow TTFB. Returns {"kind":..., "evidence":...} (evidence is the
    literal probe output) or None if the site is genuinely healthy.
    `cert_error` is checked BEFORE the generic down-status check: a TLS
    handshake failure means curl never gets an HTTP status line either
    (status ends up None, same signature as a plain down site), so
    checking cert_error first surfaces the more specific, more actionable
    "cert_warning" diagnosis instead of burying it in "site_down".

    `intent`/`permit`: re-confirming a DataMoon intent signal or permit
    record against its live source is NOT wired yet (spec decomposition
    items 2-4 / enrichment sub-project #2-3). Returns None -- NEVER a
    canned "re-confirmed" string, which would be fabricated evidence (the
    plan's Global Constraints: "Never fabricate ... a defect"). verify()
    is responsible for routing a None defect on these motions to
    NEEDS_HUMAN rather than auto-PASSing on an unconfirmed signal.
    """
    if motion != "rebuild":
        return None

    h = _probe_headers(real_domain)
    if h.get("cert_error"):
        return {
            "kind": "cert_warning",
            "evidence": h.get("cert_error_detail") or f"TLS certificate problem on https://{real_domain}",
        }
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


# ---------------------------------------------------------------------------
# Hand-off to approval_queue + CRM push (spec section 6)
#
# `_queue`/`_push_crm` wrap the REAL approval_queue/artifact/crm_router
# interfaces pinned in Task 0. They exist as thin, separately-monkeypatchable
# seams so `run()`'s own tests never touch Postgres or a live CRM.
# ---------------------------------------------------------------------------


def _queue(*, business_key: str, confidence: float, risk_level: str, content: dict, metadata: dict) -> str:
    """Queue the verified prospect as an approval_queue Artifact and return
    the resulting status ("auto_approved" | "pending_approval").

    Uses the real `services.artifact.create_artifact` factory (the
    documented production path -- Artifact itself should not be
    hand-constructed) with audience="internal"/intent="inform": this
    artifact is the internal verification record that drives the CRM
    hand-off, not prospect-facing copy, so it trivially clears the factory's
    moral gate (serves_person=True for audience="internal") rather than
    risking an unwanted "escalated" status. `risk_level` is passed as an
    explicit override, which create_artifact honors outright, so this
    module -- not the factory's audience/intent defaults -- owns the
    low/medium mapping from the verification verdict (spec section 6).

    `content` must be JSON-serializable; Artifact.content is a str field.

    NOTE: `approval_queue.queue_artifact()` returns the artifact_id, not a
    status string, so the status this function returns is read off the
    Artifact itself (already computed by create_artifact) rather than off
    queue_artifact's return value.
    """
    import json

    from services.artifact import create_artifact
    from services import approval_queue

    artifact = create_artifact(
        agent_id="sdr-verification-gate",
        business_key=business_key,
        artifact_type="crm_update",
        audience="internal",
        intent="inform",
        content=json.dumps(content),
        subject=None,
        channel_candidates=["crm"],
        confidence=confidence,
        risk_level=risk_level,
        metadata=metadata,
    )
    approval_queue.queue_artifact(artifact)
    return artifact.status


def _push_crm(*, prospects: list, business_key: str):
    from tools.crm_router import push_prospects_to_crm

    return push_prospects_to_crm(prospects, source_agent="sdr-verification-gate", business_key=business_key)


def run(req: VerificationRequest) -> dict:
    """The end-to-end entry point: verify, then hand off per spec section 6.

    PASS/NEEDS_HUMAN -> queued (low risk -> auto_approved -> CRM push
    fires immediately; medium risk -> pending_approval -> 1-click human
    queue, no CRM push yet). FAIL -> dropped + logged, never queued, never
    contacted.
    """
    res = verify(req)
    if res.verdict == "FAIL":
        return {"verdict": "FAIL", "queue_status": None, "crm": None, "reason": res.reason}

    risk = "low" if (res.verdict == "PASS" and res.confidence >= 0.75) else "medium"
    # Prospect dict for the eventual CRM push. tools/twenty.py and
    # tools/ghl.py disagree on the contact-name key ("contact" vs
    # "contact_name") and the domain key ("website" vs "domain" fallback) --
    # see the Task 0 pin at the top of this file -- so both are aliased here
    # rather than picking only the one key this task's pin names.
    contact_name = (res.verified_contact or {}).get("name")
    prospect = {
        "business_name": req.entity.get("company_name"),
        "company_name": req.entity.get("company_name"),
        "website": res.real_primary_site,
        "domain": res.real_primary_site,
        "city": req.entity.get("city", ""),
        "email": (res.verified_contact or {}).get("email"),
        "phone": (res.verified_contact or {}).get("phone"),
        "name": contact_name,
        "contact": contact_name,
        "contact_name": contact_name,
        "defect": res.verified_defect,
        "evidence": res.evidence_log,
    }
    status = _queue(
        business_key=req.business_key,
        confidence=res.confidence,
        risk_level=risk,
        content=prospect,
        metadata={"verdict": res.verdict, "reason": res.reason},
    )
    crm = _push_crm(prospects=[prospect], business_key=req.business_key) if status == "auto_approved" else None
    return {"verdict": res.verdict, "queue_status": status, "crm": crm, "reason": res.reason}
