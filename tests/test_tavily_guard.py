"""services/tavily_guard.py -- the hard ceiling. Fail closed, always."""
from unittest import mock

from services import tavily_guard as G


def test_default_cap_is_500_when_env_unset(monkeypatch):
    monkeypatch.delenv("TAVILY_HARD_CAP_CREDITS", raising=False)
    assert G.hard_cap() == 500


def test_cap_overridable_via_env(monkeypatch):
    monkeypatch.setenv("TAVILY_HARD_CAP_CREDITS", "50")
    assert G.hard_cap() == 50


def test_cap_falls_back_to_default_on_bad_env(monkeypatch):
    monkeypatch.setenv("TAVILY_HARD_CAP_CREDITS", "not-a-number")
    assert G.hard_cap() == 500


def test_under_cap_allows(monkeypatch):
    monkeypatch.setenv("TAVILY_HARD_CAP_CREDITS", "500")
    with mock.patch("services.llm_ledger.tavily_usage",
                    return_value={"plan_usage": 28, "plan_limit": 4000,
                                  "paygo_usage": 28, "paygo_limit": None}):
        allowed, reason = G.check_budget()
    assert allowed is True
    assert "28/500" in reason


def test_at_or_over_cap_blocks(monkeypatch):
    monkeypatch.setenv("TAVILY_HARD_CAP_CREDITS", "500")
    with mock.patch("services.llm_ledger.tavily_usage",
                    return_value={"plan_usage": 500, "plan_limit": 4000,
                                  "paygo_usage": 500, "paygo_limit": None}):
        allowed, reason = G.check_budget()
    assert allowed is False
    assert "cap_reached" in reason


def test_unreadable_usage_fails_closed_blocked():
    with mock.patch("services.llm_ledger.tavily_usage", return_value=None):
        allowed, reason = G.check_budget()
    assert allowed is False
    assert reason == "usage_unreadable"


def test_usage_check_exception_fails_closed_blocked():
    with mock.patch("services.llm_ledger.tavily_usage", side_effect=RuntimeError("boom")):
        allowed, reason = G.check_budget()
    assert allowed is False
    assert "usage_check_failed" in reason


def test_web_search_tool_skips_call_when_budget_blocked(monkeypatch):
    import tools.web_search as W
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    with mock.patch("services.tavily_guard.check_budget", return_value=(False, "cap_reached:500/500")):
        with mock.patch.object(W, "TavilyClient") as client_cls:
            result = W.web_search_tool.func("some query")
    assert "budget" in result.lower()
    client_cls.assert_not_called()


def test_contact_enricher_skips_call_when_budget_blocked(monkeypatch):
    import tools.contact_enricher as E
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    with mock.patch("services.tavily_guard.check_budget", return_value=(False, "cap_reached:500/500")):
        with mock.patch("tavily.TavilyClient") as client_cls:
            result = E._tavily_search("some query")
    assert result == ""
    client_cls.assert_not_called()
