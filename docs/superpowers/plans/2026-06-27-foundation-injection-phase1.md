# Servant-Leader Foundation Injection — Phase 1 Implementation Plan

> **For agentic workers:** implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The servant-leader foundation in `config/principles.py` reaches every
persona's reasoning context *before* it reasons — "running first," not as a backstop.
Close the gap where personas were loaded without the foundation.

**Architecture:** Add one `foundation_header()` assembler in `config/principles.py`
that composes the existing constants into a prompt-ready string, then prepend it to
the system prompt at both in-repo persona chokepoints, mirroring the runtime-injection
pattern of `services/current_time.py::current_time_block()`.

**Spec reference:** `docs/superpowers/specs/2026-06-27-servant-leader-foundation-and-vision.md`

---

## Task 1: `foundation_header()` assembler

**Files:** `config/principles.py`

- [x] **Step 1:** Add `foundation_header()` composing the Operating Foundation +
  `SYSTEM_IDENTITY` + `AGENT_BEHAVIORAL_CONSTRAINTS` into one prompt-ready string.
  Reuse existing constants — no duplicated text.
- [x] **Step 2:** Add a cross-repo note that the live Slack surface
  (`avo-slack/app.py`, separate repo) must mirror this helper to be covered.

## Task 2: Inject at both chokepoints

**Files:** `services/persona_prompts/__init__.py`, `services/flag_responder.py`

- [x] **Step 1:** `load_persona_prompt()` (APE) — prepend `foundation_header()` to
  the returned system prompt.
- [x] **Step 2:** `_load_persona_prompt()` (flag_responder) — prepend
  `foundation_header()` to the persona text, only when a real persona file resolves
  (preserve the `""` unknown-seat sentinel).
- [x] **Step 3:** Foundation goes in the **system prompt**, not the user message.
  `current_time_block()` stays where it is.

## Task 3: Regression guard

**Files:** `tests/test_foundation_injection.py`

- [x] **Step 1:** Assert `foundation_header()` contains the servant-leadership
  marker, composes identity + constraints, and does not duplicate the constraints
  block.
- [x] **Step 2:** Assert every APE persona prompt (`services/persona_prompts/*.md`)
  carries the marker via `load_persona_prompt()`.
- [x] **Step 3:** Assert every flag_responder seat persona carries the marker.

## Verification

- [x] `python -m unittest tests.test_foundation_injection` — all green.
- [x] Print `foundation_header()` and `load_persona_prompt('infrastructure')`;
  confirm the foundation leads the persona prompt.
- [x] Edited modules import cleanly (`config.principles`, `services.persona_prompts`,
  `services.flag_responder`).

## Out of scope / follow-up

- [ ] Mirror `foundation_header()` into `avo-slack/app.py` (separate repo) so the
  live Slack chats also lead with the foundation.
- [x] Phase 2: `VISION` / `MISSION` / `DIRECTION` constants, authored by Michael,
  folded into `foundation_header()`. (Done 2026-06-27.)
