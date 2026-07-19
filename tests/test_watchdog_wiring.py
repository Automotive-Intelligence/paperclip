from unittest import mock

import pytest

from services import watchdog


def test_slack_function_removed():
    assert not hasattr(watchdog, "_post_to_slack")


def test_run_once_does_not_post_slack(monkeypatch):
    monkeypatch.setattr(watchdog, "_all_anomalies",
                        lambda: [watchdog.Anomaly("x", "y", "warn")])
    monkeypatch.setattr(watchdog, "_active_fingerprints", lambda: set())
    monkeypatch.setattr(watchdog, "_record_active", lambda a: None)

    def _boom(*a, **k):
        raise AssertionError("requests.post must not be called (Slack removed)")

    monkeypatch.setattr(watchdog.requests, "post", _boom)
    anomalies, new = watchdog.run_once()
    assert len(new) == 1


def test_checks_registry_includes_new_checks():
    names = {c.__name__ for c in watchdog._CHECKS}
    assert "_check_blog_freshness" in names
    assert "_check_emails_sent" in names
    assert "_check_env_truth" in names


def test_env_truth_flags_non_production():
    with mock.patch.object(watchdog, "_current_environment", return_value="development"):
        out = watchdog._check_env_truth()
        assert any(a.fingerprint == "env-mislabelled" for a in out)


def test_env_truth_silent_when_production():
    with mock.patch.object(watchdog, "_current_environment", return_value="production"):
        assert watchdog._check_env_truth() == []


def test_brand_sites_read_from_config():
    cfg = {"site_urls": ["https://only-one.example"]}
    calls = []

    class _Resp:
        status_code = 200

    def _get(url, **k):
        calls.append(url)
        return _Resp()

    with mock.patch.object(watchdog, "load_watchdog_config", return_value=cfg), \
         mock.patch.object(watchdog.requests, "get", _get):
        watchdog._check_brand_sites()
    assert calls == ["https://only-one.example"]
