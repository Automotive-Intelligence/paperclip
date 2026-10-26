"""Tests for services/crew_guard.py: empty or placeholder crew output must raise."""

from types import SimpleNamespace

import pytest

from services.crew_guard import EmptyCrewOutput, checked_kickoff, validate_output


class _FakeCrew:
    def __init__(self, raw):
        self._raw = raw
        self.inputs = "unset"

    def kickoff(self, inputs=None):
        self.inputs = inputs
        usage = SimpleNamespace(total_tokens=10, prompt_tokens=8, cached_prompt_tokens=0,
                                completion_tokens=2, successful_requests=1)
        return SimpleNamespace(raw=self._raw, token_usage=usage,
                               __str__=lambda self: self.raw)


REAL = "Revenue scorecard: 3 deals in pipeline, 1 closed this week, next step is follow-up."


def test_real_output_passes_through():
    crew = _FakeCrew(REAL)
    result = checked_kickoff(crew, "alex")
    assert result.raw == REAL
    assert crew.inputs is None


def test_inputs_forwarded():
    crew = _FakeCrew(REAL)
    checked_kickoff(crew, "alex", inputs={"k": "v"})
    assert crew.inputs == {"k": "v"}


@pytest.mark.parametrize("raw", ["", "   \n", "ok"])
def test_empty_or_tiny_output_raises(raw):
    with pytest.raises(EmptyCrewOutput):
        checked_kickoff(_FakeCrew(raw), "zoe")


def test_iteration_limit_placeholder_raises():
    raw = "Agent stopped due to iteration limit or time limit. " + "x" * 50
    with pytest.raises(EmptyCrewOutput):
        validate_output("nova", raw)


def test_zero_prospects_message_is_not_empty():
    raw = ("No verifiable prospects found this run. Searched HVAC owners in Dallas, "
           "none had a public phone and website.")
    assert validate_output("tyler", raw) == raw
