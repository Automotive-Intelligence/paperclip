# Design Spec: The SDR Verification Gate

**Date:** 2026-07-27
**Author:** The Sales Desk (AVO), driving; Michael Rodriguez, owner
**Status:** approved design direction (Michael delegated the design calls), pending spec sign-off
**Sub-project of:** the autonomous SDR desk (this is sub-project #1 of 4; see Decomposition)

---

## 1. Why this exists (the one-paragraph version)

The autonomous SDR desk is ~75% already built in paperclip: signal ingestion (`datamoon_visitor_id`, `intent_inbound`, `permit_feed`), scoring (`intent_scoring`), enrichment (`tools/contact_enricher`), CRM routing (`tools/crm_router`), outreach (`intent_workflow_runner`), and a risk-based approval gate (`services/approval_queue`). One thing is missing, and it is the exact thing that failed the week of 2026-07-15: **nothing verifies that a sourced prospect is real before it becomes an opportunity or gets contacted.** Three of seven WD rebuild leads were built on stale/vanity/redirect domains whose real sites were live and fine; a fabricated "prior relationship" reached a caller-facing page; a bounce was miscounted as a reply. At autonomous send-volume, that error rate is a reputation fire. The Verification Gate is the scrub step Michael did by hand in his 20 years as an SDR, encoded, so the desk can run unattended without industrializing last week's mistakes.

## 2. Scope of THIS spec

Build one bounded module, the **Verification Gate**, that sits between scoring and CRM-write/outreach and emits a verdict + evidence that drives the existing `approval_queue`. That is all. Assembling the full always-on Railway engine, the enrichment step, and flipping on autonomous send are separate sub-projects (see Decomposition) that depend on this one and are out of scope here.

## 3. Decomposition (for context; only #1 is specced here)

1. **The Verification Gate** ← THIS SPEC. The missing scrub. Smallest, highest-leverage, and the thing that broke last week.
2. **SDR engine assembly** — wire the existing pieces into one always-on Railway loop (mirrors `services/studio_social_engine.py`).
3. **Enrichment/scrub step** — harden `contact_enricher` + a secondary source ahead of the gate.
4. **Autonomous send-on** — flip the `approval_queue` auto-dispatch on, after a shadow-mode proving run.

## 4. Contract (the unit's interface)

A bounded unit with one job. Consumers use it without reading its internals.

**Input** `VerificationRequest`:
- `business_key: str` (brand: `wd` | `avi` | `aipg` | `bookd`) — routes the eventual CRM write.
- `entity: dict` — `{company_name, domain_on_file, contact_name?, contact_phone?, contact_email?}`.
- `signal: Signal` — the `intent_scoring.Signal` that flagged this entity (source_name, tier).
- `motion: str` — `rebuild` (site-defect pitch) | `intent` (DataMoon) | `permit` (permit_feed). Determines which "defect/live-signal" check runs.

**Output** `VerificationResult`:
- `verdict: "PASS" | "FAIL" | "NEEDS_HUMAN"`
- `real_primary_site: str | None` — the resolved true primary domain (may differ from `domain_on_file`).
- `verified_defect: dict | None` — `{kind, evidence}` where evidence is the literal command output or quote.
- `verified_contact: dict | None` — `{name, phone, source}` where source ∈ {site, gbp, yelp}; never a data broker.
- `confidence: float` (0.0–1.0)
- `evidence_log: list[str]` — every check run and its raw result, for audit.
- `reason: str` — one line, human-readable, especially on FAIL/NEEDS_HUMAN.

## 5. The three checks

Deterministic where possible (pure Python, `curl`/`requests`), LLM judgment ONLY for the genuinely fuzzy call, and when the LLM is used it must cite the deterministic evidence in `evidence_log`.

**Check 1 — Real primary site** (runs for every motion)
- `curl -sIv https://{domain_on_file}` → inspect `subject: CN=` (a CN for a different host = alias, e.g. Bonick's cert was for `www.bonicklandscaping.com`) and `Location:` 301 (redirect, e.g. Stride → `stridepestcontrol.com`). Follow redirects to the terminal host.
- Fetch the page, read `<link rel="canonical">` → names the real domain.
- Web-search `"{company_name}" {city}` and read the Google Business Profile for the site they actually list.
- **Resolve to ONE canonical primary domain.** If it differs from `domain_on_file`, set `real_primary_site` to the truth and carry it forward. If the "real" site is live and modern, a `rebuild`-motion prospect FAILS here (nothing to fix).

**Check 2 — Real defect / live signal** (motion-dependent)
- `rebuild`: confirm a checkable defect ON `real_primary_site`: HTTP 404/down; TLS cert warning; no phone/CTA/contact-form on the homepage; `viewport` contains `maximum-scale=1` or `user-scalable=no` (pinch-zoom block); TTFB via `curl -w '%{time_starttransfer}'` > 1.5s. Store the exact command output as `verified_defect.evidence`.
- `intent`: re-confirm the DataMoon signal is still live/fresh (not stale) per `intent_scoring` freshness.
- `permit`: re-confirm the permit is still open/active at source.

**Check 3 — Real contact** (runs for every motion)
- A contact name plus a phone/email pulled from the company's OWN published info (`site` | `gbp` | `yelp`). Cross-check against the company. NEVER accept a data-broker number (RocketReach/ZoomInfo) as verified — that is the fabricated-enrichment trap. If only a broker number exists, `verified_contact` stays null and the contact is marked unverified.

## 6. Verdict logic → drives the existing approval_queue

The gate does not decide to send. It produces a verdict + confidence and hands an `approval_queue.Artifact` over. Mapping (matches `approval_queue`'s existing thresholds: auto at confidence ≥ 0.75 + low risk):

| Gate result | risk_level | confidence | approval_queue outcome |
|---|---|---|---|
| All 3 pass, high confidence | `low` | ≥ 0.75 | `auto_approved` → dispatch (full autonomy) |
| Passes but one check ambiguous | `medium` | < 0.75 | `pending_approval` → 1-click human queue |
| Any check FAILS | n/a | n/a | **dropped + logged, never queued, never contacted** |

This is exactly Michael's "full autonomy with a safety valve": clean prospects auto-dispatch, ambiguous ones wait for a click, garbage never reaches a person. The valve (`approval_queue`) already exists; this spec only feeds it a trustworthy verdict.

On `auto_approved`, the caller pushes the verified prospect via `tools/crm_router.push_prospects_to_crm(prospects, source_agent="sdr-verification-gate", business_key=...)`, which routes to GHL (AIPG) or the correct per-brand Twenty instance (WD/AvI/Book'd).

## 7. No lock-in, by construction

- Deterministic checks (curl/requests) are pure Python, model-independent.
- The single fuzzy judgment ("is this really their primary site?") goes through ONE LLM adapter call; swapping Claude for another model later is a one-adapter change, not a rewrite.
- The check logic and prompts live as a portable skill at `.agents/skills/prospect-verification/SKILL.md`, plain text, NOT a Claude Project.

## 8. File layout

- `services/sdr_verification_gate.py` — the module: `verify(request: VerificationRequest) -> VerificationResult`, plus the three check functions and the `approval_queue` hand-off.
- `.agents/skills/prospect-verification/SKILL.md` — the portable prompt/logic for the one LLM judgment call.
- `tests/test_sdr_verification_gate.py` — see Testing.
- Reuses (does not modify): `services/approval_queue`, `tools/crm_router`, `tools/twenty`, `tools/ghl`, `services/intent_scoring` types.

## 9. Testing — the acceptance gate

Last week handed us a golden fixture set: the seven WD rebuilds, with known-correct verdicts. Recorded fixtures (captured HTTP responses, so tests are offline and deterministic):

| Fixture | Expected verdict | Why |
|---|---|---|
| Spike Electric | PASS | our domain was their real primary site; defect (no CTA) real |
| Excalibur Pest | PASS | real primary site; pinch-zoom block real |
| Pool-ology | PASS | real primary site; TTFB 2.3s real |
| Bonick | FAIL | `domain_on_file` is a cert-alias; real site (`bonicklandscaping.com`) is live and fine |
| Stride | FAIL | `domain_on_file` 301-redirects to the real live site |
| TAPS | NEEDS_HUMAN | 404 could be genuinely-down or a wrong domain; ambiguous by design |
| Pool Pros | NEEDS_HUMAN | "offline 17 months" unverifiable by machine; flag, do not assert |

**Ship criterion:** the gate reproduces last week's correct human judgment on all seven. If it had existed, it would have passed 3, killed 2, and flagged 2. Unit tests also cover each check in isolation with fixtures for cert-mismatch, 301, canonical, viewport-block, TTFB, and broker-only-contact.

## 10. Out of scope (explicit)

- The always-on Railway loop (sub-project #2).
- Enrichment hardening (sub-project #3).
- Turning on autonomous send / the shadow run (sub-project #4).
- LinkedIn DM automation (separate; carries real account-ban risk, handled as draft-then-human, not raw login automation).
- Any change to `approval_queue`'s thresholds or `crm_router`'s routing.

## 11. Open items (owner: Michael, non-blocking for build)

1. Confirm the four `business_key` values match brand config (`wd`/`avi`/`aipg`/`bookd`).
2. Which motion leads the first live run once assembled: WD permit-feed rebuilds, or AvI DataMoon intent. (Does not block building the gate.)
