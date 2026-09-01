"""Tavily provider truth: usage fetch shape, email section, watchdog gauge."""
from unittest import mock

from services import llm_ledger as L
from services import spend_email as E
from services import watchdog


def _tv(plan=3600, limit=4000, paygo=0, cap=None):
    return {"plan_usage": plan, "plan_limit": limit, "paygo_usage": paygo, "paygo_limit": cap}


def test_email_section_flags_uncapped_paygo():
    html = E._tavily_section(_tv(paygo=920))
    assert "3600/4000" in html and "UNCAPPED" in html and "920" in html


def test_email_section_shows_cap_when_set():
    html = E._tavily_section(_tv(cap=1000))
    assert "capped at 1000" in html and "UNCAPPED" not in html


def test_email_section_empty_when_unreadable():
    assert E._tavily_section(None) == ""


def _cfg(enabled=True, pct=90):
    return {"search_credits": {"enabled": enabled, "warn_pct": pct}}


def test_watchdog_quiet_below_threshold():
    with mock.patch("services.llm_ledger.tavily_usage", return_value=_tv(plan=3000)):
        assert watchdog._check_search_credits(_cfg()) == []


def test_watchdog_warns_at_threshold_and_names_uncapped():
    with mock.patch("services.llm_ledger.tavily_usage", return_value=_tv(plan=3800)):
        out = watchdog._check_search_credits(_cfg())
    assert [a.fingerprint for a in out] == ["search-credits-high"]
    assert "uncapped" in out[0].human
    assert watchdog._runbook("search-credits-high")


def test_watchdog_skip_when_disabled_or_unreadable():
    with mock.patch("services.llm_ledger.tavily_usage") as seam:
        assert watchdog._check_search_credits(_cfg(enabled=False)) == []
        seam.assert_not_called()
    with mock.patch("services.llm_ledger.tavily_usage", return_value=None):
        assert watchdog._check_search_credits(_cfg()) == []


def test_registered_in_checks():
    assert watchdog._check_search_credits in watchdog._CHECKS
