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
    "aipg": "aiphoneguy",
    "bookd": "bookd",
}


def resolve_business_key(desk_key: str) -> str:
    """Desk key (wd/avi/aipg/bookd) -> runtime CRM key. Pure boundary
    translation -- does NOT rename the underlying `callingdigital` slug
    (spec section 4, decision 2026-07-27). Raises ValueError on an unknown
    desk key rather than silently defaulting, so a typo never misroutes."""
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

def _twenty_get_people(runtime_key: str, limit: int) -> list:
    """Hand-rolled `GET /rest/people` -- tools/twenty.py has no ready-made
    reader (Task 0). Isolated here so tests monkeypatch this one seam and
    never touch the wire."""
    import requests
    from tools.twenty import _headers, _workspace_config

    base_url, api_key = _workspace_config(runtime_key)
    r = requests.get(
        f"{base_url}/rest/people",
        headers=_headers(api_key),
        params={"limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    return (r.json().get("data") or {}).get("people") or []


def read_unverified_candidates(runtime_key: str, limit: int = 100) -> list:
    """Candidates from the brand's Twenty with no `gate-verified` tag.
    Returns `{twenty_id, company_name, domain_on_file, contact_name,
    contact_phone, contact_email, created_at, tags}` per candidate. `tags`
    (the person's existing tag list, sans `gate-verified` by definition of
    this filter) is threaded through so a downstream commit-mode write can
    append `gate-verified` to it (`_tag_verified`, finding 2) without
    clobbering any tag already on the record."""
    out = []
    for p in _twenty_get_people(runtime_key, limit):
        tags = p.get("tags") or []
        if "gate-verified" in tags:
            continue
        out.append({
            "twenty_id": p.get("id"),
            "company_name": p.get("companyName"),
            "domain_on_file": (p.get("domainName") or {}).get("primaryLinkUrl"),
            "contact_name": (p.get("name") or {}).get("firstName"),
            "contact_phone": (p.get("phones") or {}).get("primaryPhoneNumber"),
            "contact_email": (p.get("emails") or {}).get("primaryEmail"),
            "created_at": p.get("createdAt"),
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
                "confidence": res.confidence}
    queue_status = (
        "auto_approved"
        if (res.verdict == "PASS" and res.confidence >= AUTO_DISPATCH_MIN_CONFIDENCE)
        else "pending_approval"
    )
    return {"verdict": res.verdict, "queue_status": queue_status, "crm": None, "reason": res.reason,
            "confidence": res.confidence}


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


def _push_crm(*, prospects: list, business_key: str):
    """business_key MUST be the runtime key (callingdigital/autointelligence/
    aiphoneguy/bookd) -- never the raw desk key (finding 1). Every call site
    in this module passes `runtime_key`, already resolved once per run by
    `resolve_business_key`."""
    from tools.crm_router import push_prospects_to_crm

    return push_prospects_to_crm(prospects, source_agent="sdr-engine", business_key=business_key)


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


def _tag_verified(*, runtime_key: str, twenty_id: str, existing_tags: list) -> None:
    """Write the `gate-verified` tag back onto the Twenty person record so
    the next run's `read_unverified_candidates` skips it (finding 2
    IMPORTANT, code review 2026-07-27 -- without this, every scheduled run
    re-processes, and once written, re-pushes, the same candidates
    forever). COMMIT MODE ONLY: `run_sdr_engine` never calls this from a
    shadow run -- tagging is itself a write, and shadow must stay 100%
    side-effect-free. Re-processing the same read-only candidates on the
    next shadow run is acceptable; a shadow run never advances any state."""
    import requests
    from tools.twenty import _headers, _workspace_config

    if not twenty_id:
        return
    tags = list(existing_tags or [])
    if "gate-verified" not in tags:
        tags.append("gate-verified")
    base_url, api_key = _workspace_config(runtime_key)
    r = requests.patch(
        f"{base_url}/rest/people/{twenty_id}",
        headers=_headers(api_key),
        json={"tags": tags},
        timeout=20,
    )
    r.raise_for_status()


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


def run_sdr_engine(brand_key: str, source: str = "twenty_unverified", commit: bool = False) -> dict:
    """One run: pull unverified candidates from the brand's Twenty, verify
    each (the gate's pure `verify()` only -- see `_gate_run`), and route by
    verdict. commit=False (the default) is shadow mode: read-only,
    side-effect-free, records what it WOULD do -- no push, no
    approval_queue write, no tag write. commit=True is the ONLY mode that
    writes, and every write in this run is keyed by `runtime_key`, never
    the raw desk key (finding 1):

      PASS + confidence >= AUTO_DISPATCH_MIN_CONFIDENCE + crm_ready:
        push to CRM once, record auto_approved in approval_queue, tag
        gate-verified.
      PASS but low-confidence, OR crm_ready is False:
        do NOT push -- queue pending_approval in approval_queue instead
        (holds rather than misroutes), tag gate-verified.
      NEEDS_HUMAN:
        queue pending_approval in approval_queue, never push, tag
        gate-verified.
      FAIL:
        drop + log; no push, no queue, no tag (left for the next run --
        a permanently-FAIL rebuild target still isn't rebuild-worthy next
        run either, but re-tagging it is not this round's scope).
    """
    from services.sdr_verification_gate import VerificationRequest

    runtime_key = resolve_business_key(brand_key)
    counts = {"produced": 0, "pass": 0, "needs_human": 0, "fail": 0, "written": 0}
    lines = []

    for c in read_unverified_candidates(runtime_key):
        counts["produced"] += 1
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
            metadata = {"verdict": verdict, "reason": res["reason"]}
            if ready:
                _push_crm(prospects=[prospect], business_key=runtime_key)
                _queue_approval(business_key=runtime_key, confidence=confidence,
                                 risk_level="low", content=prospect, metadata=metadata)
                counts["written"] += 1
                note = "wrote"
            else:
                _queue_approval(business_key=runtime_key, confidence=confidence,
                                 risk_level="medium", content=prospect, metadata=metadata)
                note = "queued pending"
            _tag_verified(runtime_key=runtime_key, twenty_id=c.get("twenty_id"),
                          existing_tags=c.get("tags") or [])
        else:
            note = "would write" if (auto_eligible and crm_ready(runtime_key)) else "would hold pending"

        lines.append(f"- {c.get('company_name')} | {verdict} | {res['reason']} | {note}")

    mode = "COMMIT" if commit else "SHADOW"
    body_lines = [f"# SDR engine run [{mode}]: {brand_key}", ""]
    body_lines += lines or ["- no unverified candidates found"]
    body_lines += ["", f"Counts: {counts}"]
    digest = "\n".join(body_lines)

    digest_path = _write_digest(digest, brand_key, commit)

    return {**counts, "digest": digest, "digest_path": digest_path}
