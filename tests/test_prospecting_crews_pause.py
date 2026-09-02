"""Tyler/Marcus/Ryan Data pause (Sales Desk 30-day accountability review,
2026-09-01): fails CLOSED, and none may spend a single Tavily credit while
paused -- the crew function itself must never be called.
"""
from unittest import mock

import app


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PROSPECTING_CREWS_ENABLED", raising=False)
    assert app._prospecting_crews_enabled() is False


def test_enabled_only_on_exact_string_one(monkeypatch):
    monkeypatch.setenv("PROSPECTING_CREWS_ENABLED", "true")
    assert app._prospecting_crews_enabled() is False
    monkeypatch.setenv("PROSPECTING_CREWS_ENABLED", "1")
    assert app._prospecting_crews_enabled() is True


def test_tyler_paused_never_calls_crew(monkeypatch):
    monkeypatch.delenv("PROSPECTING_CREWS_ENABLED", raising=False)
    with mock.patch.object(app, "_run_tyler_crew") as crew:
        out = app.run_tyler_prospecting()
    crew.assert_not_called()
    assert out["status"] == "paused"


def test_marcus_paused_never_calls_crew(monkeypatch):
    monkeypatch.delenv("PROSPECTING_CREWS_ENABLED", raising=False)
    with mock.patch.object(app, "_run_marcus_crew") as crew:
        out = app.run_marcus_prospecting()
    crew.assert_not_called()
    assert out["status"] == "paused"


def test_ryan_data_paused_never_calls_crew(monkeypatch):
    monkeypatch.delenv("PROSPECTING_CREWS_ENABLED", raising=False)
    with mock.patch.object(app, "_run_ryan_data_crew") as crew:
        out = app.run_ryan_data_prospecting()
    crew.assert_not_called()
    assert out["status"] == "paused"


def test_marcus_vertical_override_ignored_while_paused(monkeypatch):
    monkeypatch.delenv("PROSPECTING_CREWS_ENABLED", raising=False)
    with mock.patch.object(app, "_run_marcus_crew") as crew:
        out = app.run_marcus_prospecting(vertical_override="pi-law")
    crew.assert_not_called()
    assert out["status"] == "paused"


def test_re_enabling_lets_tyler_run(monkeypatch):
    monkeypatch.setenv("PROSPECTING_CREWS_ENABLED", "1")
    with mock.patch.object(app, "_run_tyler_crew", return_value="No verifiable prospects found this run"):
        with mock.patch.object(app, "persist_log"):
            with mock.patch.object(app, "_execute_sales_pipeline",
                                   return_value={"status": "ok", "parsed_prospects": 0}):
                out = app.run_tyler_prospecting()
    assert out.get("status") != "paused"
