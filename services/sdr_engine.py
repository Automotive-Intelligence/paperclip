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

   IMPORTANT REAL-WORLD DEVIATION FROM THE SP2 SPEC'S ASSUMPTION: `run()`
   does NOT merely return a verdict for the caller to act on -- it ALREADY
   performs the write. For any PASS/NEEDS_HUMAN verdict it unconditionally
   calls the private `_queue()` seam (creates a real `approval_queue`
   Artifact via `services.artifact.create_artifact` + `approval_queue.
   queue_artifact`), and when that queue status comes back "auto_approved"
   it ALSO unconditionally calls the private `_push_crm()` seam (a REAL
   live CRM write via `tools.crm_router.push_prospects_to_crm`) -- verified
   against source and against
   tests/test_sdr_verification_gate.py::test_pass_auto_approves_and_pushes,
   which asserts exactly this. The SP2 spec's section 6 step 4 phrasing
   ("PASS auto_approved -> if commit, crm_router.push_prospects_to_crm(...);
   if shadow, record 'would write'") assumes `run()` itself never writes --
   that assumption is FALSE for the real, already-shipped gate.

   Given this module's binding global constraint (shadow must be provably
   side-effect-free: NO crm write, NO send, and -- by the same spirit --
   no approval_queue write either), `_gate_run` (Task 3) does NOT call the
   real `run()` when commit=False. It calls the gate's pure `verify()`
   function instead (also pinned, real, no side effects: runs the three
   checks and returns a `VerificationResult`, touches only the network
   probes + the one LLM judgment call -- never approval_queue, never CRM),
   and maps the result onto the same `{verdict, queue_status, crm, reason}`
   shape `run()` returns, using approval_queue's OWN published auto-dispatch
   threshold (`services.artifact.AUTO_DISPATCH_MIN_CONFIDENCE`, currently
   0.75) rather than a value re-guessed here -- this exactly reproduces
   `run()`'s own `risk = "low" if (verdict=="PASS" and confidence>=0.75)
   else "medium"` -> `_derive_status` auto_approved/pending_approval mapping
   for every verdict/confidence pair `verify()` can actually produce today
   (PASS always confidence 0.85, NEEDS_HUMAN always confidence 0.5, FAIL
   short-circuits before confidence matters). commit=True calls the real
   `run()` -- the one and only place any write happens, and only once the
   caller (this engine, gated by its OWN `commit` flag) has decided to
   write.

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
    contact_phone, contact_email, created_at}` per candidate."""
    out = []
    for p in _twenty_get_people(runtime_key, limit):
        if "gate-verified" in (p.get("tags") or []):
            continue
        out.append({
            "twenty_id": p.get("id"),
            "company_name": p.get("companyName"),
            "domain_on_file": (p.get("domainName") or {}).get("primaryLinkUrl"),
            "contact_name": (p.get("name") or {}).get("firstName"),
            "contact_phone": (p.get("phones") or {}).get("primaryPhoneNumber"),
            "contact_email": (p.get("emails") or {}).get("primaryEmail"),
            "created_at": p.get("createdAt"),
        })
    return out
