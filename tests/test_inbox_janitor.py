"""Inbox janitor: archive-only noise sweep with dry-run default."""
from unittest import mock

from services import inbox_janitor


def _cfg():
    return {"enabled": True, "label": "Machines", "accounts": {
        "salesdroid": [
            {"name": "github-ci", "query": "cc:ci_activity@noreply.github.com"},
            {"name": "empty-query", "query": ""},
        ]}}


def test_disabled_config_no_ops():
    with mock.patch.object(inbox_janitor, "_load_config", return_value={"enabled": False}):
        out = inbox_janitor.run_sweep(commit=True)
    assert out["ok"] and out["skipped"]


def test_unreadable_config_no_ops():
    with mock.patch.object(inbox_janitor, "_load_config", return_value={}):
        assert inbox_janitor.run_sweep(commit=True)["skipped"]


def test_dry_run_reports_but_never_mutates():
    hits = [{"id": "t1"}, {"id": "t2"}]
    with mock.patch.object(inbox_janitor, "_load_config", return_value=_cfg()), \
         mock.patch("services.postal_inbox.search", return_value=hits) as srch, \
         mock.patch("services.postal_inbox.apply_label") as lab, \
         mock.patch("services.postal_inbox.archive") as arc:
        out = inbox_janitor.run_sweep(commit=False)
    assert out["would_move"] == 2 and out["commit"] is False
    lab.assert_not_called()
    arc.assert_not_called()
    # in:inbox is enforced on every query; empty-query rule is skipped entirely
    assert srch.call_count == 1
    assert srch.call_args[0][1].startswith("in:inbox ")


def test_commit_labels_then_archives_and_heartbeats():
    hits = [{"id": "t1"}]
    with mock.patch.object(inbox_janitor, "_load_config", return_value=_cfg()), \
         mock.patch("services.postal_inbox.search", return_value=hits), \
         mock.patch("services.postal_inbox.apply_label") as lab, \
         mock.patch("services.postal_inbox.archive") as arc, \
         mock.patch("services.watchdog.record_heartbeat") as hb:
        out = inbox_janitor.run_sweep(commit=True)
    assert out["moved"] == 1
    lab.assert_called_once_with("salesdroid", "t1", "Machines")
    arc.assert_called_once_with("salesdroid", "t1")
    hb.assert_called_once_with("inbox_janitor")


def test_search_failure_isolated_per_rule():
    cfg = {"enabled": True, "accounts": {"salesdroid": [
        {"name": "a", "query": "from:a"}, {"name": "b", "query": "from:b"}]}}
    calls = iter([RuntimeError("gmail down"), [{"id": "t9"}]])

    def _search(*a, **k):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    with mock.patch.object(inbox_janitor, "_load_config", return_value=cfg), \
         mock.patch("services.postal_inbox.search", side_effect=_search), \
         mock.patch("services.postal_inbox.apply_label"), \
         mock.patch("services.postal_inbox.archive"), \
         mock.patch("services.watchdog.record_heartbeat"):
        out = inbox_janitor.run_sweep(commit=True)
    assert out["errors"] == 1 and out["moved"] == 1


def test_move_failure_never_sinks_the_sweep():
    with mock.patch.object(inbox_janitor, "_load_config", return_value=_cfg()), \
         mock.patch("services.postal_inbox.search", return_value=[{"id": "t1"}, {"id": "t2"}]), \
         mock.patch("services.postal_inbox.apply_label",
                    side_effect=[RuntimeError("quota"), None]), \
         mock.patch("services.postal_inbox.archive") as arc, \
         mock.patch("services.watchdog.record_heartbeat"):
        out = inbox_janitor.run_sweep(commit=True)
    assert out["moved"] == 1 and out["errors"] == 1
    arc.assert_called_once()


def test_real_config_parses_and_is_salesdroid_scoped():
    cfg = inbox_janitor._load_config()
    assert cfg.get("enabled") is True
    accounts = cfg.get("accounts") or {}
    assert set(accounts) == {"salesdroid"}
    for rule in accounts["salesdroid"]:
        assert rule.get("name") and rule.get("query")
