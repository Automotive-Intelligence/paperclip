"""watchdog._check_monitor_freshness: disabled, fresh, stale, unknown."""
from unittest import mock

from services import watchdog


def _cfg(tp=0, gm=0):
    return {"monitors": {"tp_daily_max_age_hours": tp, "growth_monitor_max_age_hours": gm}}


def test_disabled_by_default_probes_nothing():
    with mock.patch.object(watchdog, "_latest_dated_block") as seam:
        assert watchdog._check_monitor_freshness(_cfg(0, 0)) == []
        seam.assert_not_called()


def test_fresh_tp_daily_no_anomaly():
    with mock.patch.object(watchdog, "_latest_dated_block", return_value="2026-07-20"), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 20, 12, tzinfo=watchdog.timezone.utc)):
        assert watchdog._check_monitor_freshness(_cfg(tp=30)) == []


def test_stale_tp_daily_flags():
    with mock.patch.object(watchdog, "_latest_dated_block", return_value="2026-07-15"), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 20, tzinfo=watchdog.timezone.utc)):
        out = watchdog._check_monitor_freshness(_cfg(tp=30))
        assert any(a.fingerprint == "monitor-stale-tp_daily" for a in out)


def test_unknown_when_no_block():
    with mock.patch.object(watchdog, "_latest_dated_block", return_value=None):
        out = watchdog._check_monitor_freshness(_cfg(gm=30))
        assert any(a.fingerprint == "monitor-freshness-unknown-growth_monitor" for a in out)


def test_regex_matches_both_dash_variants():
    tp_path, tp_re = watchdog._MONITOR_BLOCKS["tp_daily"]
    assert tp_re.findall("## 🏁 TP daily -- 2026-07-20\n## 🏁 TP daily — 2026-07-21") == ["2026-07-20", "2026-07-21"]


def test_registered_in_checks():
    assert watchdog._check_monitor_freshness in watchdog._CHECKS
