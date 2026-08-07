"""Tier 1 self-healing: one attempt, grace hold, cooldown, receipts."""
from datetime import datetime, timedelta, timezone
from unittest import mock

from services import watchdog, watchdog_remediation as rem


def _now():
    return datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


def _cfg(enabled=True, cooldown=20):
    return {"auto_remediation": {"enabled": enabled, "cooldown_hours": cooldown}}


def _anom(fp="monitor-stale-tp_daily"):
    return watchdog.Anomaly(fp, "stale", "warn")


def test_disabled_touches_nothing():
    with mock.patch.object(rem, "_last_attempt") as seam:
        held, amended = rem.maybe_remediate([_anom()], _cfg(enabled=False), _now())
    assert held == set() and amended == {}
    seam.assert_not_called()


def test_unmapped_fingerprint_alerts_normally():
    held, amended = rem.maybe_remediate(
        [_anom("blog-stale-avi")], _cfg(), _now())
    assert held == set() and amended == {}


def test_first_detection_attempts_fix_and_holds_one_sweep():
    with mock.patch.object(rem, "_last_attempt", return_value=None), \
         mock.patch.object(rem, "_log_attempt") as log, \
         mock.patch.object(rem, "action_for",
                           return_value=("rerun-tp-daily", lambda: "receipt")):
        held, amended = rem.maybe_remediate([_anom()], _cfg(), _now())
    assert held == {"monitor-stale-tp_daily"} and amended == {}
    log.assert_called_once()
    args = log.call_args[0]
    assert args[0] == "monitor-stale-tp_daily" and args[2] is True


def test_persisting_after_attempt_amends_not_holds():
    with mock.patch.object(rem, "_last_attempt", return_value=_now() - timedelta(hours=2)), \
         mock.patch.object(rem, "_log_attempt") as log, \
         mock.patch.object(rem, "action_for",
                           return_value=("rerun-tp-daily", lambda: "receipt")):
        held, amended = rem.maybe_remediate([_anom()], _cfg(), _now())
    assert held == set()
    assert "auto-fix 'rerun-tp-daily' attempted 2h ago" in amended["monitor-stale-tp_daily"]
    log.assert_not_called()  # cooldown: never a retry loop


def test_cooldown_expiry_allows_a_fresh_attempt():
    with mock.patch.object(rem, "_last_attempt", return_value=_now() - timedelta(hours=25)), \
         mock.patch.object(rem, "_log_attempt"), \
         mock.patch.object(rem, "action_for",
                           return_value=("rerun-tp-daily", lambda: "receipt")):
        held, amended = rem.maybe_remediate([_anom()], _cfg(), _now())
    assert held == {"monitor-stale-tp_daily"}


def test_failed_fix_alerts_immediately_with_receipt():
    def _boom():
        raise RuntimeError("engine down")
    with mock.patch.object(rem, "_last_attempt", return_value=None), \
         mock.patch.object(rem, "_log_attempt") as log, \
         mock.patch.object(rem, "action_for", return_value=("rerun-tp-daily", _boom)):
        held, amended = rem.maybe_remediate([_anom()], _cfg(), _now())
    assert held == set()
    assert "FAILED just now" in amended["monitor-stale-tp_daily"]
    assert log.call_args[0][2] is False


def test_db_error_degrades_to_normal_alerting():
    with mock.patch.object(rem, "_last_attempt", side_effect=RuntimeError("db")), \
         mock.patch.object(rem, "action_for",
                           return_value=("rerun-tp-daily", lambda: "receipt")):
        held, amended = rem.maybe_remediate([_anom()], _cfg(), _now())
    assert held == set() and amended == {}


def test_run_once_holds_healed_and_amends_failed():
    a_fix = _anom()
    a_other = _anom("blog-stale-avi")
    with mock.patch.object(watchdog, "_all_anomalies", return_value=[a_fix, a_other]), \
         mock.patch("services.watchdog_remediation.maybe_remediate",
                    return_value=({"monitor-stale-tp_daily"}, {"blog-stale-avi": " [amended]"})), \
         mock.patch.object(watchdog, "_active_fingerprints", return_value=set()), \
         mock.patch.object(watchdog, "_record_active"), \
         mock.patch.object(watchdog, "record_heartbeat"):
        anomalies, new = watchdog.run_once()
    fps = [a.fingerprint for a in anomalies]
    assert fps == ["blog-stale-avi"]
    assert anomalies[0].human.endswith(" [amended]")


def test_every_registered_action_maps_to_known_fingerprint_classes():
    for prefix, name, thunk in rem._ACTIONS:
        assert prefix.startswith(("monitor-stale-", "service-heartbeat-"))
        assert callable(thunk) and name
    # money/outbound/content-spend classes must NEVER be auto-fixed
    for forbidden in ("emails-sent-zero", "blog-stale-avi", "postal-inbox-reauth-avi",
                      "slipstream-queue-exhausted-avi", "vercel-deploy-blocked-x"):
        assert rem.action_for(forbidden) is None
