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
