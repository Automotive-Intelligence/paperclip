"""services/sdr_engine.py -- the SDR Engine (shadow mode).

Sub-project #2 of the autonomous SDR desk (see
docs/superpowers/specs/2026-07-27-sdr-engine-shadow-design.md, governed by
docs/superpowers/specs/sdr-desk-principles.md, BINDING). Reads unverified
candidates from a brand's Twenty, runs each through the Verification Gate
(sub-project #1, services/sdr_verification_gate.py), and routes verified
opportunities to the right CRM. Shadow (commit=False) is the DEFAULT: a run
always produces a digest of what it would do; nothing is written live unless
commit=True. Mirrors services/studio_social_engine.py's dry-run-default
pattern.

The ONLY signal source THIS sub-project reads is `source="twenty_unverified"`
-- unverified people already sitting in a brand's TWENTY workspace. That
covers `wd`/`avi`/`bookd` (callingdigital/autointelligence/bookd all have a
Twenty workspace, per tools/twenty.py). `aipg` (aiphoneguy) is a real desk
key and stays in `_DESK_TO_RUNTIME` -- but AIPG's CRM is GHL, and there is
no aiphoneguy Twenty workspace to read from (code review round 3, finding 2
IMPORTANT: `read_unverified_candidates("aiphoneguy")` used to raise an
opaque `ValueError`, 500-ing the route, despite the route/module surface
implying aipg was servable). `run_sdr_engine` now refuses honestly instead:
for a brand whose runtime CRM this source can't read from, it returns its
normal digest+counts shape with an explicit "source not available" line and
zero counts -- never an uncaught exception. A GHL-read source for aipg is a
later sub-project.

---------------------------------------------------------------------------
PINNED INTERFACES (Task 0 -- read from source 2026-07-27, do not guess)
---------------------------------------------------------------------------

1. services/sdr_verification_gate.py

   `run(req: VerificationRequest) -> dict` returns
   `{"verdict": "PASS"|"FAIL"|"NEEDS_HUMAN", "queue_status": str|None,
   "crm": <push result>|None, "reason": str}`.

   `VerificationRequest` is a plain dataclass with fields, IN ORDER:
   `business_key: str` (comment in source: "brand: wd | avi | aipg | bookd"
   -- i.e. the gate's OWN contract expects the DESK key here, not the
   runtime/Twenty-workspace key), `entity: dict`
   (`company_name`, `domain_on_file`, `contact_name?`, `contact_phone?`,
   `contact_email?`), `signal: Any`, `motion: str` ("rebuild" is the only
   motion with a wired Check 2 today).

   IMPORTANT REAL-WORLD DEVIATION FROM THE SP2 SPEC'S ASSUMPTION, tightened
   after code review (2026-07-27, finding 1 CRITICAL): `run()` does NOT
   merely return a verdict for the caller to act on -- it ALREADY performs
   the write, INTERNALLY KEYED BY WHATEVER `VerificationRequest.business_key`
   IT WAS BUILT WITH. For any PASS/NEEDS_HUMAN verdict it unconditionally
   calls the private `_queue()` seam (creates a real `approval_queue`
   Artifact via `services.artifact.create_artifact` + `approval_queue.
   queue_artifact`), and when that queue status comes back "auto_approved"
   it ALSO unconditionally calls the private `_push_crm()` seam (a REAL
   live CRM write via `tools.crm_router.push_prospects_to_crm(...,
   business_key=req.business_key)`) -- verified against source and against
   tests/test_sdr_verification_gate.py::test_pass_auto_approves_and_pushes,
   which asserts exactly this.

   This module's own contract builds `VerificationRequest.business_key` from
   the RAW DESK KEY (per the gate's own field comment: "brand: wd | avi |
   aipg | bookd" -- entity/motion/signal are the fields this engine actually
   needs verified; business_key here is carried through only because the
   dataclass requires it positionally). If this engine ever called the
   gate's `run()`, that desk key ("wd") would flow straight into
   `crm_router.push_prospects_to_crm(business_key="wd")` ->
   `resolve_crm_provider` -> `business_crm_map.get("wd", "ghl")` -- "wd" is
   not a `business_crm_map` key, so it SILENTLY DEFAULTS TO GHL (AIPG's
   CRM). A WD/AvI PASS+auto_approved candidate would misroute into the
   wrong brand's CRM, entirely bypassing this engine's own `crm_ready`
   gate -- AND this engine would then perform its OWN, correctly-keyed
   push right after, so every such candidate would be written TWICE (once
   wrong, once right).

   THE FIX (confined to this module; the gate itself is never modified,
   spec section 10): **this engine never calls the gate's `run()`, in
   either mode.** `_gate_run` calls only the gate's pure `verify()`
   function (pinned, real, no side effects: runs the three checks and
   returns a `VerificationResult` -- touches only the network probes + the
   one LLM judgment call, never `approval_queue`, never CRM) and maps the
   result onto a `{verdict, queue_status, crm, reason, confidence}` shape,
   using approval_queue's OWN published auto-dispatch threshold
   (`services.artifact.AUTO_DISPATCH_MIN_CONFIDENCE`, currently 0.75)
   rather than a value re-guessed here -- this reproduces `run()`'s own
   `risk = "low" if (verdict=="PASS" and confidence>=0.75) else "medium"`
   -> auto_approved/pending_approval mapping for PASS verdicts (queue_status
   is only ever "auto_approved" for an explicit PASS -- NEEDS_HUMAN is
   never auto-eligible, matching the gate's own rule that only PASS is
   auto-eligible). `run_sdr_engine` (Task 3, revised) then owns the ONE
   write path itself, ALWAYS using the already-resolved `runtime_key`
   (never the desk key) for both the CRM push (`_push_crm`) and the
   approval_queue record (`_queue_approval`, this module's own mirror of
   the gate's `_queue()` -- same real `create_artifact` + `queue_artifact`
   factory calls, `agent_id="sdr-engine"` instead of
   `"sdr-verification-gate"` so the two callers' records are distinguishable,
   `business_key=runtime_key`). commit=False (shadow) performs neither --
   see `run_sdr_engine` for the full write/no-write matrix.

2. tools/twenty.py

   No public "list/read people" helper exists. The real per-workspace
   base_url/api_key resolver is the PRIVATE `_workspace_config(business_key)
   -> (base_url, api_key)` (raises ValueError if unconfigured), keyed on the
   RUNTIME slugs `callingdigital` / `autointelligence` / `bookd` via the
   module's `_WORKSPACE_URL_ENV` / `_WORKSPACE_KEY_ENV` dicts + `os.environ`
   (the plan's guessed `_base_url_for`/`_api_key_for` names do not exist).
   `_headers(api_key) -> dict` builds the Bearer + Content-Type headers.
   `_twenty_get_people` (Task 2) hand-rolls the `GET /rest/people` call
   using these two real private seams, since no ready-made reader exists.
   There is likewise no ready-made tag-writer; `_tag_verified` (finding 2,
   code review 2026-07-27) hand-rolls a `PATCH /rest/people/{id}` using the
   same two seams, writing back the `tags` array Twenty already returns on
   each person (read in `read_unverified_candidates` and threaded through
   as `candidate["tags"]` precisely so this write can append to it instead
   of clobbering any tags a person already carries).

3. config/runtime.py

   No `settings.crm_ready_for` method exists (the plan's guess is wrong),
   and there is no bare module-level `settings` object -- only the
   `@lru_cache` singleton factory `get_settings() -> RuntimeSettings`.
   `RuntimeSettings` exposes `twenty_wd_ready` / `twenty_avi_ready` /
   `twenty_bookd_ready` (bools), the `ghl_ready` property, `business_crm_map`
   (dict, default `{"aiphoneguy": "ghl", "callingdigital": "twenty",
   "autointelligence": "twenty", "bookd": "twenty"}`), and the method
   `crm_provider_ready(provider, business_key=None) -> bool` which already
   dispatches ghl -> `ghl_ready`, twenty -> the per-workspace
   `twenty_ready_for_business(business_key)` (itself keying
   callingdigital->twenty_wd_ready, autointelligence->twenty_avi_ready,
   bookd->twenty_bookd_ready). `crm_ready()` (Task 1) composes
   `business_crm_map` + `crm_provider_ready` rather than hand-coding the
   per-key attribute names a second time, so it stays correct if
   `BUSINESS_CRM_MAP` is ever overridden via env.

4. tools/crm_router.py

   `push_prospects_to_crm(prospects: list, source_agent: str,
   business_key: str) -> Tuple[str, list]` -- confirmed real signature,
   matches the plan.

5. services/studio_social_engine.py

   `_commit_files_to_main(files: Dict[str, str], message: str, token: str)
   -> None` -- a direct GitHub Contents-API PUT of each file to the
   `avo-telemetry` main branch (state repo, no PR gate). The existing
   caller convention (`run_week`) sources `token` from the
   `SLIPSTREAM_GH_TOKEN` env var and returns an explicit error rather than
   attempting a network call when it is unset; `_commit_receipt` (Task 4)
   follows the same convention.
---------------------------------------------------------------------------
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

# --------------------------------------------------------------------------- business_key normalization (spec section 4)

_DESK_TO_RUNTIME = {
    "wd": "callingdigital",
    "avi": "autointelligence",
    "aipg": "aiphoneguy",  # real brand/CRM mapping -- NOT servable by twenty_unverified, see below
    "bookd": "bookd",
}

# Runtime keys with an actual Twenty workspace this source can read (Task 0 /
# tools/twenty.py's _WORKSPACE_KEY_ENV: callingdigital/autointelligence/bookd
# only). aiphoneguy is GHL-backed and deliberately excluded -- run_sdr_engine
# checks this before ever calling read_unverified_candidates, so an
# unservable brand gets an honest refusal digest, not an opaque ValueError
# 500 (code review round 3, finding 2 IMPORTANT).
_TWENTY_SOURCED_RUNTIME_KEYS = frozenset({"callingdigital", "autointelligence", "bookd"})


def resolve_business_key(desk_key: str) -> str:
    """Desk key (wd/avi/aipg/bookd) -> runtime CRM key. Pure boundary
    translation -- does NOT rename the underlying `callingdigital` slug
    (spec section 4, decision 2026-07-27). Raises ValueError on an unknown
    desk key rather than silently defaulting, so a typo never misroutes.
    NOTE: a valid desk key here does not guarantee `run_sdr_engine` can
    actually read candidates for it -- `aipg` resolves fine but is
    GHL-backed, not Twenty-backed; see `_TWENTY_SOURCED_RUNTIME_KEYS`."""
    try:
        return _DESK_TO_RUNTIME[(desk_key or "").strip().lower()]
    except KeyError:
        raise ValueError(f"unknown desk business key: {desk_key!r}")


def crm_ready(runtime_key: str) -> bool:
    """True iff the runtime key's mapped CRM provider is fully configured.
    Composes the real `business_crm_map` + `crm_provider_ready` (Task 0 --
    `settings.crm_ready_for` does not exist)."""
    from config.runtime import get_settings

    settings = get_settings()
    provider = settings.business_crm_map.get(runtime_key, "ghl")
    return settings.crm_provider_ready(provider, business_key=runtime_key)


# --------------------------------------------------------------------------- read unverified candidates from Twenty

def _twenty_get_companies(runtime_key: str, limit: int) -> list:
    """Hand-rolled `GET /rest/companies` -- tools/twenty.py has no ready-made
    reader. Isolated here so tests monkeypatch this one seam and never touch
    the wire. Companies (not people) are the rebuild-motion signal: a company
    record carries `name` + `domainName.primaryLinkUrl`, exactly the two
    fields the gate's site/defect checks consume. People records in this
    workspace carry no company/domain, which is why an earlier `people` read
    produced all-null candidates."""
    import requests
    from tools.twenty import _headers, _workspace_config

    base_url, api_key = _workspace_config(runtime_key)
    r = requests.get(
        f"{base_url}/rest/companies",
        headers=_headers(api_key),
        params={"limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    return (r.json().get("data") or {}).get("companies") or []


# Obvious test/junk records seeded into the CRM -- never real prospects.
_JUNK_NAME_MARKERS = ("delete-me", "smoke-test", "probeco")
_JUNK_DOMAIN_HOSTS = frozenset({
    "gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com",
    "aol.com", "live.com", "protonmail.com", "notion.com", "stripe.com",
})
# Our OWN brand domains -- never prospect ourselves (AvI's own domain was
# sitting in its company list). Matched on the registrable domain so
# subdomains/www are covered by _domain_host's www-strip + endswith check.
_OUR_DOMAINS = frozenset({
    "automotiveintelligence.io", "worshipdigital.co", "worshipdigital.com",
    "callingdigital.com", "aiphoneguy.com", "aiphoneguy.ai", "bookd.cx",
    "buildagentempire.com", "paperandpurpose.com", "customeradvocate.io",
})


def _domain_host(url: str) -> str:
    """Bare host of a domain-on-file URL, lowercased (scheme/path stripped)."""
    h = (url or "").strip().lower()
    h = h.split("://", 1)[-1]
    h = h.split("/", 1)[0]
    return h[4:] if h.startswith("www.") else h


def _strip_scheme(url: str) -> str:
    """Domain-on-file the gate can probe: leading http(s):// scheme(s)
    removed, host+path kept. Twenty stores e.g. 'https://acme.com/x'; the
    gate's Check 1 prepends its own 'https://', so a scheme left here yields
    'https://https://acme.com/x' -- a probe of the literal host 'https' that
    always fails and forces NEEDS_HUMAN. Strip repeatedly to also heal any
    already-doubled value in the data."""
    s = (url or "").strip()
    while True:
        low = s.lower()
        if low.startswith("https://"):
            s = s[8:]
        elif low.startswith("http://"):
            s = s[7:]
        else:
            return s


def _is_our_domain(host: str) -> bool:
    """True if host is one of our own brand domains (registrable domain or a
    subdomain of it) -- we never prospect ourselves."""
    return any(host == d or host.endswith("." + d) for d in _OUR_DOMAINS)


def _is_junk_company(name: str, host: str) -> bool:
    n = (name or "").strip().lower()
    if any(m in n for m in _JUNK_NAME_MARKERS):
        return True
    if host in _JUNK_DOMAIN_HOSTS:
        return True
    if _is_our_domain(host):
        return True
    if host.endswith((".example", ".test", ".invalid")):
        return True
    if host == "example.com" or host.endswith(".example.com"):
        return True
    return False


def read_unverified_candidates(runtime_key: str, limit: int = 100) -> list:
    """Rebuild-motion candidates from the brand's Twenty COMPANIES with a real
    domain and no `gate-verified` tag. Returns `{twenty_id, company_name,
    domain_on_file, contact_name, contact_phone, contact_email, created_at,
    tags}` per candidate. A company with no domain-on-file, or an obvious
    test/junk seed, is skipped -- nothing to verify. Contact fields are null
    at read time (a bare company carries no person), so the gate verifies the
    real primary site and defect and fails closed on the missing contact
    rather than inventing one. `tags` is threaded through for the commit-mode
    `gate-verified` write; companies in this workspace carry no tag field, so
    it is an empty list -- dedup for a live company source is a follow-up to
    settle before `commit=True`."""
    out = []
    for c in _twenty_get_companies(runtime_key, limit):
        tags = c.get("tags") or []
        if "gate-verified" in tags:
            continue
        domain = (c.get("domainName") or {}).get("primaryLinkUrl")
        host = _domain_host(domain)
        if not host:
            continue
        if _is_junk_company(c.get("name"), host):
            continue
        out.append({
            "twenty_id": c.get("id"),
            "company_name": c.get("name"),
            "domain_on_file": _strip_scheme(domain),
            "contact_name": None,
            "contact_phone": None,
            "contact_email": None,
            "created_at": c.get("createdAt"),
            "tags": tags,
        })
    return out


# --------------------------------------------------------------------------- the gate + write seams (separately monkeypatchable)

def _gate_run(*, request) -> dict:
    """Thin seam over the Verification Gate's PURE `verify()` -- NEVER the
    gate's write-performing `run()` (finding 1 CRITICAL, code review
    2026-07-27; see the Task 0 pin block for why). This engine owns every
    write itself, always keyed by the resolved runtime_key -- `run_sdr_engine`
    is the only place a push or a queue happens, in both modes.

    `queue_status` is "auto_approved" only for an explicit PASS at or above
    approval_queue's real auto-dispatch confidence threshold -- matching the
    gate's own rule that ONLY PASS is auto-eligible (a NEEDS_HUMAN verdict
    is never auto_approved, regardless of its confidence value)."""
    from services.artifact import AUTO_DISPATCH_MIN_CONFIDENCE
    from services.sdr_verification_gate import verify as gate_verify

    res = gate_verify(request)
    if res.verdict == "FAIL":
        return {"verdict": "FAIL", "queue_status": None, "crm": None, "reason": res.reason,
                "defect": res.verified_defect, "site": res.real_primary_site,
                "contact": res.verified_contact,
                "confidence": res.confidence}
    queue_status = (
        "auto_approved"
        if (res.verdict == "PASS" and res.confidence >= AUTO_DISPATCH_MIN_CONFIDENCE)
        else "pending_approval"
    )
    return {"verdict": res.verdict, "queue_status": queue_status, "crm": None, "reason": res.reason,
            "confidence": res.confidence,
            "defect": res.verified_defect, "site": res.real_primary_site,
            "contact": res.verified_contact}


def _prospect_dict(c: dict) -> dict:
    """Build the CRM-push/approval_queue prospect payload from a
    read_unverified_candidates() record. Aliases contact-name/domain keys
    both ways -- tools/twenty.py and tools/ghl.py disagree on the field
    name (see services/sdr_verification_gate.py's own Task 0 pin)."""
    contact_name = c.get("contact_name")
    return {
        "business_name": c.get("company_name"),
        "company_name": c.get("company_name"),
        "website": c.get("domain_on_file"),
        "domain": c.get("domain_on_file"),
        "email": c.get("contact_email"),
        "phone": c.get("contact_phone"),
        "name": contact_name,
        "contact": contact_name,
        "contact_name": contact_name,
    }


def _queue_approval(*, business_key: str, confidence: float, risk_level: str,
                     content: dict, metadata: dict) -> str:
    """This module's own mirror of the gate's private `_queue()`
    (services/sdr_verification_gate.py) -- same real production factory
    (`services.artifact.create_artifact` + `approval_queue.queue_artifact`),
    but keyed by the resolved RUNTIME key (never the desk key the gate's
    own internal call would have used) and `agent_id="sdr-engine"` so the
    two callers' approval_queue records are distinguishable. Returns the
    artifact's status ("auto_approved" | "pending_approval"), read off the
    Artifact itself (create_artifact already computed it) -- NOT
    queue_artifact's return value, which is the artifact_id."""
    import json

    from services import approval_queue
    from services.artifact import create_artifact

    artifact = create_artifact(
        agent_id="sdr-engine",
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


def _company_has_opportunity(*, runtime_key: str, company_id: str) -> bool:
    """True if the brand's Twenty already has an opportunity for this company.
    This is our dedup for a company-sourced desk: companies carry no `tags`
    field (people do), so the old people-style `gate-verified` tag is not
    available -- an existing opportunity for the company IS the durable
    'already worked, do not re-create' signal, and it survives across runs."""
    import requests
    from tools.twenty import _headers, _workspace_config

    if not company_id:
        return False
    base_url, api_key = _workspace_config(runtime_key)
    r = requests.get(
        f"{base_url}/rest/opportunities",
        headers=_headers(api_key),
        params={"filter": f"companyId[eq]:{company_id}", "limit": 1},
        timeout=20,
    )
    r.raise_for_status()
    return bool((r.json().get("data") or {}).get("opportunities"))


def _opportunity_name(company_name: str, defect: "dict | None") -> str:
    """Honest opportunity title -- the verified defect IS the rebuild pitch,
    taken straight from the gate's verified_defect (never fabricated)."""
    base = (company_name or "Opportunity").strip()
    kind = (defect or {}).get("kind")
    label = f"{base} | Website Rebuild (SDR-verified: {kind})" if kind \
        else f"{base} | Website Rebuild (SDR-verified)"
    return label[:240]


def _write_opportunity(*, runtime_key: str, company_id: str, company_name: str,
                       defect: "dict | None") -> str:
    """Create a DEDUPED rebuild opportunity for a PASS company. Returns
    'exists' (an opportunity is already there -> skip; this is the dedup that
    keeps repeat/scheduled runs from duplicating) or 'created:<id>'. COMMIT
    MODE ONLY -- `run_sdr_engine` never calls this from a shadow run. The
    company_id is the candidate's twenty_id: we read /rest/companies, so the
    candidate id already IS the company id -- no name/domain search needed."""
    import requests
    from tools.twenty import _headers, _workspace_config

    if not company_id:
        raise ValueError("no company_id for opportunity write")
    if _company_has_opportunity(runtime_key=runtime_key, company_id=company_id):
        return "exists"
    base_url, api_key = _workspace_config(runtime_key)
    r = requests.post(
        f"{base_url}/rest/opportunities",
        headers=_headers(api_key),
        json={"name": _opportunity_name(company_name, defect), "companyId": company_id},
        timeout=20,
    )
    r.raise_for_status()
    opp = (r.json().get("data") or {}).get("createOpportunity") or {}
    return f"created:{opp.get('id')}"


# --------------------------------------------------------------------------- the digest receipt (Task 4)

_RECEIPT_DIR = "marketing_deliverables/sdr_engine"  # mirrors studio_social_engine's _DELIVERABLES pattern


def _commit_receipt(path: str, body: str) -> None:
    """Publish one digest to avo-telemetry main via the real, pinned
    `studio_social_engine._commit_files_to_main`. Never attempts a network
    call without a token -- raises instead, same convention as
    `studio_social_engine.run_week`."""
    from services.studio_social_engine import _commit_files_to_main

    token = os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SLIPSTREAM_GH_TOKEN missing; cannot publish sdr-engine receipt")
    _commit_files_to_main({path: body}, f"sdr-engine: {path}", token)


def _write_digest(digest: str, brand_key: str, commit: bool) -> str:
    """A dated receipt path is always returned, in BOTH modes. Only
    commit=True actually publishes it (shadow is provably side-effect-free:
    no receipt is committed either)."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = f"{_RECEIPT_DIR}/sdr_shadow_{brand_key}_{run_id}.md"
    if commit:
        _commit_receipt(path, digest)
    return path


# --------------------------------------------------------------------------- the loop core (Task 3)

_VERDICT_KEY = {"PASS": "pass", "NEEDS_HUMAN": "needs_human", "FAIL": "fail"}


def _empty_counts() -> dict:
    return {"produced": 0, "pass": 0, "needs_human": 0, "fail": 0, "written": 0, "errors": 0}


def _finish(brand_key: str, commit: bool, counts: dict, lines: list) -> dict:
    """Shared digest-assembly + receipt tail, used by every return path
    (normal completion, the source-unavailable refusal, and a total read
    failure) so all three always produce a real digest/receipt -- shadow
    mode must always yield a digest (code review round 3, finding 1)."""
    mode = "COMMIT" if commit else "SHADOW"
    body_lines = [f"# SDR engine run [{mode}]: {brand_key}", ""]
    body_lines += lines or ["- no unverified candidates found"]
    body_lines += ["", f"Counts: {counts}"]
    digest = "\n".join(body_lines)
    digest_path = _write_digest(digest, brand_key, commit)
    return {**counts, "digest": digest, "digest_path": digest_path}


def run_sdr_engine(brand_key: str, source: str = "twenty_unverified", commit: bool = False) -> dict:
    """One run: pull unverified candidates from the brand's Twenty, verify
    each (the gate's pure `verify()` only -- see `_gate_run`), and route by
    verdict. commit=False (the default) is shadow mode: read-only,
    side-effect-free, records what it WOULD do -- no opportunity write, no
    approval_queue write, no receipt publish. commit=True is the ONLY mode
    that writes, and every write in this run is keyed by `runtime_key`, never
    the raw desk key (finding 1):

      PASS + confidence >= AUTO_DISPATCH_MIN_CONFIDENCE + crm_ready:
        create ONE deduped rebuild opportunity for the company
        (`_write_opportunity`; skipped if the company already has one),
        record auto_approved in approval_queue.
      PASS but low-confidence, OR crm_ready is False:
        do NOT write an opportunity -- queue pending_approval in
        approval_queue instead (holds rather than misroutes).
      NEEDS_HUMAN:
        queue pending_approval in approval_queue, never write an opportunity.
      FAIL:
        drop + log; no opportunity, no queue.

    This function NEVER lets one bad candidate, or a total read failure,
    kill the run (code review round 3, finding 1 IMPORTANT -- widened from
    round 2, which only isolated the write step):

    - If `source` isn't actually readable for this brand (aipg/aiphoneguy
      is GHL-backed, no Twenty workspace -- finding 2 IMPORTANT), this
      returns immediately with an honest "source not available" digest and
      all-zero counts. `read_unverified_candidates` is never called, so its
      real `ValueError: no workspace mapping` never reaches the caller.
    - If the upfront `read_unverified_candidates(...)` call itself raises
      (a total read failure -- Twenty down, bad credentials, etc.), that is
      caught too: the digest records "read failed: <exception>" with zero
      candidates, and the run returns normally. Shadow mode must always
      yield a digest -- that digest is the entire point (Michael reads it).
    - PER CANDIDATE, the gate call (`_gate_run` -- curl probes, an HTTP
      fetch, and the one LLM judgment call, which the gate DESIGNS to raise
      on a down/unreachable site) through the full write sequence
      (opportunity write, queue) all live in ONE try/except. An exception
      before a verdict was ever determined is a "verify-failed" entry,
      tallied under `errors` -- NOT under pass/needs_human/fail/written, so
      it can never inflate the real verdict counts. An exception AFTER the
      verdict was already determined (the write sequence itself) is a
      "write-failed" entry -- the verdict bucket it already earned stays
      counted, but `written` does not increment for it. Either way the loop
      `continue`s to the next candidate; the run completes and the digest
      is produced.

    Dedup for a company-sourced desk is opportunity-existence, not a tag:
    `_write_opportunity` returns "exists" (skip, not counted written) when
    the company already has an opportunity, so repeat/scheduled runs never
    duplicate. A candidate that fails mid-write is safely retried next run;
    the existence check then guards against a real duplicate.
    """
    from services.sdr_verification_gate import VerificationRequest

    runtime_key = resolve_business_key(brand_key)
    counts = _empty_counts()

    if source == "twenty_unverified" and runtime_key not in _TWENTY_SOURCED_RUNTIME_KEYS:
        lines = [
            f"- source {source!r} not available for {brand_key!r} "
            f"({runtime_key}, GHL-backed); no candidates read"
        ]
        return _finish(brand_key, commit, counts, lines)

    try:
        candidates = read_unverified_candidates(runtime_key)
    except Exception as exc:  # a total read failure must still yield a digest
        counts["errors"] += 1
        lines = [f"- read failed: {type(exc).__name__}: {exc}"]
        return _finish(brand_key, commit, counts, lines)

    lines = []

    for c in candidates:
        counts["produced"] += 1
        verdict = None
        try:
            req = VerificationRequest(
                business_key=brand_key,
                entity={
                    "company_name": c.get("company_name"),
                    "domain_on_file": c.get("domain_on_file"),
                    "contact_name": c.get("contact_name"),
                    "contact_phone": c.get("contact_phone"),
                    "contact_email": c.get("contact_email"),
                },
                signal=None,
                motion="rebuild",
            )
            res = _gate_run(request=req)
            verdict = res["verdict"]
            counts[_VERDICT_KEY[verdict]] += 1

            if verdict == "FAIL":
                lines.append(f"- {c.get('company_name')} | {verdict} | {res['reason']} | drop")
                continue

            auto_eligible = (verdict == "PASS" and res["queue_status"] == "auto_approved")
            confidence = res.get("confidence", 0.85 if verdict == "PASS" else 0.5)

            if commit:
                ready = auto_eligible and crm_ready(runtime_key)
                prospect = _prospect_dict(c)
                if ready:
                    result = _write_opportunity(
                        runtime_key=runtime_key,
                        company_id=c.get("twenty_id"),
                        company_name=c.get("company_name"),
                        defect=res.get("defect"),
                    )
                    _queue_approval(business_key=runtime_key, confidence=confidence,
                                     risk_level="low", content=prospect,
                                     metadata={"verdict": verdict, "reason": res["reason"],
                                               "opportunity": result})
                    if result.startswith("created:"):
                        counts["written"] += 1
                        note = f"wrote opportunity ({result})"
                    else:
                        note = "opportunity already exists (dedup skip)"
                else:
                    _queue_approval(business_key=runtime_key, confidence=confidence,
                                     risk_level="medium", content=prospect,
                                     metadata={"verdict": verdict, "reason": res["reason"]})
                    note = "queued pending"
            else:
                note = "would write opportunity" if (auto_eligible and crm_ready(runtime_key)) else "would hold pending"

            lines.append(f"- {c.get('company_name')} | {verdict} | {res['reason']} | {note}")

        except Exception as exc:  # noqa: BLE001 -- one candidate must never abort the batch
            if verdict is None:
                counts["errors"] += 1
                lines.append(
                    f"- {c.get('company_name')} | verify-failed: {type(exc).__name__}: {exc} "
                    f"(twenty_id={c.get('twenty_id')})"
                )
            else:
                lines.append(
                    f"- {c.get('company_name')} | {verdict} | write-failed: "
                    f"{type(exc).__name__}: {exc} (twenty_id={c.get('twenty_id')})"
                )
            continue

    return _finish(brand_key, commit, counts, lines)
