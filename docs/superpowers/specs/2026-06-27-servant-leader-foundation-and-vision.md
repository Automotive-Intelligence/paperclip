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

## Phase 2 — Vision / Mission / Direction (pending Michael's words)

- Capture, in Michael's words: **Vision** (where AVO is going), **Mission** (what it
  does and for whom), **Direction** (the F1 operating model — each business a
  purpose-built car, lean/focused/energetic-to-win, none alike).
- Add `VISION`, `MISSION`, `DIRECTION` constants to `config/principles.py`.
- Fold them into `foundation_header()` so they reach every persona automatically.

---

## Verification

- **Automated:** `python -m unittest tests.test_foundation_injection` — every persona
  loader output must contain the marker; header composes without duplication.
- **Behavioral:** print `foundation_header()` and `load_persona_prompt(<persona>)`;
  confirm the foundation now *leads* the persona prompt.
- **Regression guard:** the test fails loudly if anyone disconnects the injection.
