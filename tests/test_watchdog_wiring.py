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
    assert "_check_slipstream_queues" in names


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


# ---------------------------------------------------------------------------
# Slipstream queue-depth check (_check_slipstream_queues). An exhausted/low topic
# queue makes the blog engine HOLD SILENTLY -- produce nothing every run (the
# 2026-07-31 BAE-class miss). This check reads queue depth on its own rail so a
# draining queue is a VISIBLE anomaly with refill runway.
# ---------------------------------------------------------------------------

_SQ_BRANDS = [
    ("agentempire", "salesdroid/avo-telemetry", "scripts/blog_queues/bae_topics.md"),
    ("autointelligence", "salesdroid/automotive-intelligence", "automation/content-queue.md"),
]


def _sq_counts(mapping):
    """A fake _fetch_queue_unchecked_count driven by a {path: count-or-None} map."""
    def _f(repo, path):
        return mapping.get(path)
    return _f


def test_slipstream_queues_disabled_when_min_unchecked_zero():
    cfg = {"slipstream_queues": {"min_unchecked": 0}}
    with mock.patch.object(watchdog, "_slipstream_brand_queues", return_value=_SQ_BRANDS):
        assert watchdog._check_slipstream_queues(cfg) == []


def test_slipstream_queues_alerts_on_exhausted():
    cfg = {"slipstream_queues": {"min_unchecked": 3, "severity": "warn"}}
    mapping = {"scripts/blog_queues/bae_topics.md": 0,        # exhausted
               "automation/content-queue.md": 28}             # healthy
    with mock.patch.object(watchdog, "_slipstream_brand_queues", return_value=_SQ_BRANDS), \
         mock.patch.object(watchdog, "_fetch_queue_unchecked_count", side_effect=_sq_counts(mapping)):
        out = watchdog._check_slipstream_queues(cfg)
    fps = {a.fingerprint for a in out}
    assert "slipstream-queue-exhausted-agentempire" in fps
    assert not any("autointelligence" in fp for fp in fps)   # healthy brand silent
    assert all(a.severity == "warn" for a in out)


def test_slipstream_queues_alerts_on_low():
    cfg = {"slipstream_queues": {"min_unchecked": 3}}
    mapping = {"scripts/blog_queues/bae_topics.md": 2,        # low (0 < n <= 3)
               "automation/content-queue.md": 28}
    with mock.patch.object(watchdog, "_slipstream_brand_queues", return_value=_SQ_BRANDS), \
         mock.patch.object(watchdog, "_fetch_queue_unchecked_count", side_effect=_sq_counts(mapping)):
        out = watchdog._check_slipstream_queues(cfg)
    assert {a.fingerprint for a in out} == {"slipstream-queue-low-agentempire"}
    assert "2 unchecked" in out[0].human


def test_slipstream_queues_unreadable_is_skipped_not_alarmed():
    """A None count (network / auth / 404) is logged + skipped, never a false alarm."""
    cfg = {"slipstream_queues": {"min_unchecked": 3}}
    mapping = {"scripts/blog_queues/bae_topics.md": None,     # unreadable
               "automation/content-queue.md": 1}              # low -> should alert
    with mock.patch.object(watchdog, "_slipstream_brand_queues", return_value=_SQ_BRANDS), \
         mock.patch.object(watchdog, "_fetch_queue_unchecked_count", side_effect=_sq_counts(mapping)):
        out = watchdog._check_slipstream_queues(cfg)
    assert {a.fingerprint for a in out} == {"slipstream-queue-low-autointelligence"}


def test_slipstream_brand_queues_reads_enabled_only(tmp_path, monkeypatch):
    """_slipstream_brand_queues returns (key, queue_repo|repo, queue_path) for
    ENABLED brands only, defaulting queue_repo to repo."""
    p = tmp_path / "slipstream_brands.yaml"
    p.write_text(
        "brands:\n"
        "  a:\n    enabled: true\n    repo: org/a\n    queue_path: q/a.md\n"
        "  b:\n    enabled: true\n    repo: org/b\n    queue_repo: org/queues\n    queue_path: q/b.md\n"
        "  c:\n    enabled: false\n    repo: org/c\n    queue_path: q/c.md\n"
    )
    monkeypatch.setattr(watchdog, "_SLIPSTREAM_BRANDS_PATH", str(p))
    got = watchdog._slipstream_brand_queues()
    assert ("a", "org/a", "q/a.md") in got
    assert ("b", "org/queues", "q/b.md") in got              # queue_repo overrides repo
    assert not any(k == "c" for k, _, _ in got)              # disabled brand excluded
