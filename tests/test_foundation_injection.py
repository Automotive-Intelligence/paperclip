"""Regression guard: is the servant-leader foundation still running?

The AIBOS foundation (servant leadership + identity + behavioral constraints)
and the canonical Vision / Mission / Direction only shape behavior if they reach
the model's reasoning context *before* it reasons. config/principles.py composes
them once in foundation_header(); both in-repo persona loaders prepend that to the
system prompt:

  - services/persona_prompts/__init__.py::load_persona_prompt  (APE executor)
  - services/flag_responder.py::_load_persona_prompt           (Slack-channel seats)

These tests assert stable markers survive in foundation_header() and in every
persona loader's output. If anyone ever disconnects the foundation again, this
fails loudly — it is the permanent answer to "are we still running the foundation?"

Run:  python3 -m unittest tests.test_foundation_injection

CROSS-REPO NOTE: the *live* Slack chats run from a separate repo (avo-slack/app.py)
not covered here. That mirror is tracked as out-of-scope follow-up in
config/principles.py::foundation_header.
"""

from __future__ import annotations

import unittest

from config.principles import (
    _FOUNDATION_MARKER,
    foundation_header,
)
from services.persona_prompts import load_persona_prompt
from services.flag_responder import _SEAT_TO_SLUG, _load_persona_prompt

# Stable substrings that must appear wherever the foundation is injected.
# Grouped by what they prove. If you intentionally reword the foundation, update
# the marker here in lock-step — do not delete the assertion.
FOUNDATION_MARKERS = (
    _FOUNDATION_MARKER,            # servant leadership statement
    "NO MANIPULATION",            # behavioral constraints reached context
)

VMD_MARKERS = (
    "#1 AI Operating System",     # VISION
    "discovery documents",        # MISSION
    "THE STANDARD",               # DIRECTION — what good looks like
    "Operations and delivery",    # DIRECTION — one of the six pillars
    "THE CORE LOOP",              # DIRECTION — the operating loop
    "Predictive intelligence",    # DIRECTION — full Intelligence Stack
    "Bookd",                      # DIRECTION — full six-car garage
    "Monthly Recurring Revenue",  # DIRECTION — the scoreboard (MRR north star)
    "Fields West",                # DIRECTION — honest North Star
    "act as if in excellence",    # DIRECTION — the integrity line
)

ALL_MARKERS = FOUNDATION_MARKERS + VMD_MARKERS


class FoundationHeaderTests(unittest.TestCase):
    def test_header_contains_every_marker(self):
        header = foundation_header()
        for marker in ALL_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, header)

    def test_constraints_precede_direction(self):
        # DIRECTION references "the no-manipulation constraint above", so the
        # behavioral constraints must be composed before DIRECTION.
        header = foundation_header()
        self.assertLess(
            header.index("NO MANIPULATION"),
            header.index("THE STANDARD"),
            "behavioral constraints must appear before DIRECTION",
        )


class ApeLoaderTests(unittest.TestCase):
    """services/persona_prompts/__init__.py — APE executor prompts."""

    def test_infrastructure_prompt_carries_foundation(self):
        prompt = load_persona_prompt("infrastructure")
        for marker in ALL_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, prompt)

    def test_foundation_leads_the_prompt(self):
        # The foundation runs first, not as a backstop — it should precede the
        # persona's own role-specific body.
        prompt = load_persona_prompt("infrastructure")
        self.assertLess(prompt.index(_FOUNDATION_MARKER), len(foundation_header()) + 5)


class FlagResponderLoaderTests(unittest.TestCase):
    """services/flag_responder.py — every Slack-channel persona seat."""

    def test_every_seat_carries_foundation(self):
        self.assertTrue(_SEAT_TO_SLUG, "seat registry is empty — nothing to guard")
        for seat in _SEAT_TO_SLUG:
            prompt = _load_persona_prompt(seat)
            self.assertTrue(
                prompt, f"seat {seat!r} returned empty prompt (missing persona file?)"
            )
            for marker in ALL_MARKERS:
                with self.subTest(seat=seat, marker=marker):
                    self.assertIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()
