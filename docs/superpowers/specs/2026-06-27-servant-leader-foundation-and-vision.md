# Servant-Leader Foundation + Vision/Mission/Direction — Design Spec

**Date:** 2026-06-27
**Author:** AVO (Claude Opus 4.8) via working session with Michael Rodriguez
**Status:** Phase 1 implemented; Phase 2 pending Michael-authored content
**Driver:** Make AVO a world-class AI operating system for the businesses, run
like an F1 team — each business a purpose-built race car, none alike — on a
servant-leader foundation that the system actually *runs first*.

---

## Context & problem

AVO (the AIBOS — AI Business Operating System, named from the Hebrew *Avoda*:
work, worship, and service as one) is built on a servant-leader foundation. Before
designing vision/mission/direction, Michael asked the right question first: *"are
we still running the servant-leader foundation?"*

An audit answered: **it was running at the gate, not in the drivers' heads.**

- The foundation is fully written and canonical in `config/principles.py`
  (`# AIBOS Operating Foundation`, `SYSTEM_IDENTITY`, `AGENT_BEHAVIORAL_CONSTRAINTS`,
  `DEVELOPMENT_STANDARDS`, plus the executable `evaluate_action_morally()` moral
  gate). Unchanged since it was introduced (2026-05-13).
- It is **actively enforced** only in the artifact/approval pipeline
  (`services/artifact.py` calls `evaluate_action_morally()`).
- **But it never entered any persona's reasoning context.** None of the 11 persona
  files in `services/personas/` contained it, and neither persona loader prepended
  it. The module docstring *claimed* "imports from this module make the constraints
  live in the LLM's prompt context" — but for personas that wiring was never
  connected. So when AVO reasoned and acted as a persona, it was not operating under
  the servant-leader constraint; the constraint only caught things later, as a
  backstop.

This violates the founding intent: the foundation should shape decisions *before*
they're made, not just veto them after.

---

## Decisions locked with Michael

### Decision 1: Foundation first, then vision
Close the injection gap (Phase 1) before authoring vision/mission/direction (Phase 2).

### Decision 2: `config/principles.py` is the single source of truth
Everything — foundation today, vision/mission/direction tomorrow — lives as
canonical, importable, injectable Python in `config/principles.py`. Docs are
derivative, not primary.

### Decision 3: Run it through the superpowers spec→plan process
Same discipline as the APE work (`docs/superpowers/specs/` + `plans/`).

### Decision 4: Michael authors the vision/mission/direction language
Servant leadership means serving *his* vision. Phase 2 opens by capturing his words;
AVO does not fabricate them.

---

## Architecture

### The two in-repo chokepoints

The foundation must reach both persona-loading paths in this repo:

1. `services/persona_prompts/__init__.py` → `load_persona_prompt()` — APE executor
   prompts (`services/persona_prompts/*.md`). Consumed by `persona_executor.py`.
2. `services/flag_responder.py` → `_load_persona_prompt()` — the 11 Slack-channel
   personas (`services/personas/*.md`). Consumed by the autonomous draft producer.

### Mechanism (mirror the existing pattern)

`services/current_time.py::current_time_block()` already prepends a runtime block to
persona prompts. We mirror that exact pattern with a new
`config/principles.py::foundation_header()` that assembles the existing constants
(Operating Foundation + `SYSTEM_IDENTITY` + `AGENT_BEHAVIORAL_CONSTRAINTS`) into one
prompt-ready string, prepended to the **system prompt** at load time. One
composition point; Phase 2 extends the function rather than touching call sites.

### Cross-repo note (out of scope, flagged not fixed)

The live Slack chats run from a **separate repo** (`avo-slack/app.py`); the persona
files here are mirrored from it. Those live sessions are not covered by this change.
Closing the gap for the live surface means mirroring `foundation_header()` into
avo-slack. Tracked as follow-up.

### Where the vision currently lives (the Phase 2 gap)

The APE design spec references `project_avo_vision.md` — which lives in avo-telemetry,
*outside* this repo. Phase 2 makes vision/mission/direction canonical and in-code.

---

## Phase 1 — Foundation injection (implemented)

- `config/principles.py`: add `foundation_header()` assembler (reuses constants).
- `services/persona_prompts/__init__.py`: prepend it in `load_persona_prompt()`.
- `services/flag_responder.py`: prepend it in `_load_persona_prompt()`.
- `tests/test_foundation_injection.py`: assert every persona loader output contains
  the servant-leadership marker, and that the header composes without duplication.
  This is the permanent, automated answer to "are we still running the foundation?"

## Phase 2 — Vision / Mission / Direction (implemented 2026-06-27)

Authored by Michael in a working session, then shaped into canonical text he signed
off on. Added as `VISION`, `MISSION`, `DIRECTION` constants in `config/principles.py`
and folded into `foundation_header()` (after the behavioral constraints, so
DIRECTION's "no-manipulation constraint above" reference stays accurate). Both
persona loaders pick it up automatically — no call-site changes.

**Locked decisions:**
- **Vision:** the #1 AI Operating System; every client achieves their calling,
  purpose, and goals (scale / growth / acquisition).
- **Mission:** take a client's discovery documents and use the Intelligence Stack to
  make their calling come true; build for the underdog SMB.
- **Direction:** the garage (3 cars — AI Phone Guy, Worship Digital, Automotive
  Intelligence) + the Intelligence Stack engine (psychological intelligence fenced
  under the no-manipulation constraint) + the **MRR scoreboard** + a **North Star +
  honest bridge** (20+ clients/car and a Fields West deposit, stated alongside the
  honest near-zero starting line and the first milestone) + lean/focused/
  energetic-to-win.

**Honesty-first note:** the canonical DIRECTION names the real starting line (a
handful of early clients, MRR near zero, two cars with no paying recurring client
yet). The vision is a North Star; the text never inflates the present.

**Deepened DIRECTION (Phase 2.1, 2026-06-27):** DIRECTION was upgraded from a list
into a capability map and operating model. The Intelligence Stack now defines what
each of the 11 intelligences does for the client (Michael's verbatim five —
competitive, brand, revenue-scaling, marketing, psychological — plus six confirmed
additions: financial, sales, operational, data & measurement, relationship,
predictive). A named **CORE LOOP** makes the mission's mechanism explicit (intake
discovery documents → apply the Intelligence Stack → execute → prove the outcome
against the client's calling). The garage gains per-car function and the honest
current-client roster (Worship Digital: Paper & Purpose, Panda, Warden; the AI
Phone Guy and Automotive Intelligence cars marked as not yet carrying a paying
recurring client). Tests guard the new "THE CORE LOOP" and "Predictive
intelligence" markers.

`tests/test_foundation_injection.py` now also guards the vision markers
("#1 AI Operating System", "discovery documents", "Monthly Recurring Revenue",
"Fields West") and that psychological intelligence stays fenced by the
no-manipulation constraint.

---

## Verification

- **Automated:** `python -m unittest tests.test_foundation_injection` — every persona
  loader output must contain the marker; header composes without duplication.
- **Behavioral:** print `foundation_header()` and `load_persona_prompt(<persona>)`;
  confirm the foundation now *leads* the persona prompt.
- **Regression guard:** the test fails loudly if anyone disconnects the injection.
