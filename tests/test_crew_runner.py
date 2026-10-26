"""Guards for the 2026-10-21 LLM cost fix (services/crew_runner.py).

CrewAI reuses an agent's executor across kickoffs and appends every run's
prompts to executor.messages, so long-lived agent singletons re-sent all prior
history on every run. These tests keep that from coming back.
"""

import pathlib
import re

import pytest

from services import crew_runner

REPO = pathlib.Path(__file__).resolve().parent.parent


class _FakeAgent:
    def __init__(self):
        self.role = "fake"
        self.agent_executor = object()


class _FakeCrew:
    def __init__(self, agents):
        self.agents = agents
        self.kicked = 0

    def kickoff(self):
        self.kicked += 1
        return "ran"


def test_reset_drops_every_agents_executor():
    a, b = _FakeAgent(), _FakeAgent()
    crew_runner.reset_agent_history(_FakeCrew([a, b]))
    assert a.agent_executor is None and b.agent_executor is None


def test_kickoff_fresh_resets_then_runs(monkeypatch):
    monkeypatch.setattr(crew_runner, "month_to_date_spend_usd", lambda: 1.0)
    agent = _FakeAgent()
    crew = _FakeCrew([agent])
    assert crew_runner.kickoff_fresh(crew) == "ran"
    assert agent.agent_executor is None
    assert crew.kicked == 1


def test_kickoff_fresh_skips_at_monthly_cap(monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_CAP_USD", "50")
    monkeypatch.setattr(crew_runner, "month_to_date_spend_usd", lambda: 50.01)
    crew = _FakeCrew([_FakeAgent()])
    out = crew_runner.kickoff_fresh(crew, job="darrell")
    assert crew.kicked == 0
    assert "Skipped darrell" in out and "LLM_MONTHLY_CAP_USD" in out


def test_kickoff_fresh_runs_when_ledger_unreadable(monkeypatch):
    monkeypatch.setattr(crew_runner, "month_to_date_spend_usd", lambda: None)
    crew = _FakeCrew([_FakeAgent()])
    assert crew_runner.kickoff_fresh(crew) == "ran"


def test_no_bare_crew_kickoff_outside_runner():
    """Every crew must start through kickoff_fresh, or history piles up again."""
    offenders = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if rel.parts[0] in {"tests", ".venv", "venv", "node_modules"} or rel == pathlib.Path("services/crew_runner.py"):
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\.kickoff\(\)", code):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "use services.crew_runner.kickoff_fresh(crew):\n" + "\n".join(offenders)


def test_crewai_still_rebuilds_a_dropped_executor():
    """The fix relies on CrewAI building a new executor when agent_executor is None."""
    core = pytest.importorskip("crewai.agent.core")
    src = pathlib.Path(core.__file__).read_text()
    assert "if self.agent_executor is not None:" in src, (
        "CrewAI changed how it reuses executors; re-check services/crew_runner.reset_agent_history"
    )
