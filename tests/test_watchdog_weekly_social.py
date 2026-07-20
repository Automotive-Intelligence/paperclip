"""Weekly SOCIAL batch freshness check.

The Studio weekly engine has no live URL to probe (posts schedule into Zernio,
not a public page), so the truth signal is the committed deliverable folder
`marketing_deliverables/<n>_studio_weekly_<Mon-date>`. The check is disabled
(max_age_hours: 0) until the social-engine cloud cutover, because the current
laptop engine does not reliably commit its folder; enabling it now would false-
alarm on a healthy laptop run. These tests pin the parse + the age math + the
disabled-by-default behaviour so cutover is a config flip, not a code change.
"""
from unittest import mock

from services import watchdog


def _cfg(hours):
    return {"weekly_social": {"max_age_hours": hours, "severity": "warn"}}


def test_disabled_when_hours_zero_does_not_even_probe():
    # Disabled must short-circuit BEFORE any network call.
    with mock.patch.object(watchdog, "_latest_weekly_batch_monday") as seam:
        assert watchdog._check_weekly_social_freshness(_cfg(0)) == []
        seam.assert_not_called()


def test_fresh_batch_within_threshold_no_anomaly():
    with mock.patch.object(watchdog, "_latest_weekly_batch_monday", return_value="2026-07-20"), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 22, tzinfo=watchdog.timezone.utc)):
        assert watchdog._check_weekly_social_freshness(_cfg(240)) == []


def test_future_dated_batch_is_fresh():
    # The engine produces NEXT week's batch, so a Monday in the future is the
    # healthy steady state (negative age), never an anomaly.
    with mock.patch.object(watchdog, "_latest_weekly_batch_monday", return_value="2026-07-27"), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 20, tzinfo=watchdog.timezone.utc)):
        assert watchdog._check_weekly_social_freshness(_cfg(240)) == []


def test_stale_batch_flags():
    # Newest batch covers week-of 07-06; by 07-25 that is ~19 days old > 240h.
    with mock.patch.object(watchdog, "_latest_weekly_batch_monday", return_value="2026-07-06"), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 25, tzinfo=watchdog.timezone.utc)):
        out = watchdog._check_weekly_social_freshness(_cfg(240))
        assert any(a.fingerprint == "weekly-social-stale" for a in out)


def test_no_batch_found_flags_unknown_when_enabled():
    with mock.patch.object(watchdog, "_latest_weekly_batch_monday", return_value=None):
        out = watchdog._check_weekly_social_freshness(_cfg(240))
        assert any(a.fingerprint == "weekly-social-freshness-unknown" for a in out)


def test_unparseable_date_flags_unknown():
    with mock.patch.object(watchdog, "_latest_weekly_batch_monday", return_value="not-a-date"):
        out = watchdog._check_weekly_social_freshness(_cfg(240))
        assert any(a.fingerprint == "weekly-social-freshness-unknown" for a in out)


def test_latest_batch_monday_picks_newest_dir_ignores_files_and_non_matches():
    # Handles both name variants ('<mon>' and '<mon>_to_<sun>'), ignores files
    # and unrelated deliverable folders, and returns the MAX Monday.
    payload = [
        {"type": "dir", "name": "104_studio_weekly_2026-07-06_to_2026-07-12"},
        {"type": "dir", "name": "141_studio_weekly_2026-07-20"},
        {"type": "dir", "name": "118_studio_weekly_2026-07-13"},
        {"type": "dir", "name": "142_cutting_room_video_worker_audit_and_plan"},  # unrelated dir
        {"type": "file", "name": "999_studio_weekly_2999-01-01.md"},              # a FILE, not a dir
    ]

    class _R:
        ok = True
        status_code = 200

        def json(self):
            return payload

    with mock.patch.object(watchdog.requests, "get", return_value=_R()):
        assert watchdog._latest_weekly_batch_monday() == "2026-07-20"


def test_latest_batch_monday_falls_back_to_slipstream_token(monkeypatch):
    # GITHUB_TOKEN/GH_TOKEN are absent on Railway; SLIPSTREAM_GH_TOKEN reads the
    # private avo-telemetry repo. The seam must use it, or the check 404s (the
    # real cutover bug 2026-07-20).
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "slip_tok")
    captured = {}

    class _R:
        ok = True
        status_code = 200

        def json(self):
            return [{"type": "dir", "name": "144_studio_weekly_2026-07-27"}]

    def fake_get(url, headers=None, timeout=None):
        captured["auth"] = (headers or {}).get("Authorization")
        return _R()

    with mock.patch.object(watchdog.requests, "get", side_effect=fake_get):
        assert watchdog._latest_weekly_batch_monday() == "2026-07-27"
    assert captured["auth"] == "Bearer slip_tok"


def test_latest_batch_monday_none_on_http_error():
    class _R:
        ok = False
        status_code = 404
        text = "not found"

        def json(self):
            return []

    with mock.patch.object(watchdog.requests, "get", return_value=_R()):
        assert watchdog._latest_weekly_batch_monday() is None


def test_registered_in_checks():
    assert watchdog._check_weekly_social_freshness in watchdog._CHECKS
