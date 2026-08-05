"""The watchdog-of-watchdogs layer: service heartbeats, alert-rail freshness,
acks, runbooks, and the direct-email fallback for a dead rail."""
from datetime import datetime, timedelta, timezone
from unittest import mock

from services import watchdog


def _now():
    return datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)


# ---- service heartbeats -----------------------------------------------------

def _hb_cfg(hours=3):
    return {"heartbeats": {"sonar_inbox_max_age_hours": hours}}


def test_heartbeats_disabled_probes_nothing():
    with mock.patch.object(watchdog, "_heartbeat_ts") as seam:
        assert watchdog._check_service_heartbeats({"heartbeats": {}}) == []
        assert watchdog._check_service_heartbeats(_hb_cfg(0)) == []
        seam.assert_not_called()


def test_fresh_heartbeat_no_anomaly():
    with mock.patch.object(watchdog, "_heartbeat_ts", return_value=_now() - timedelta(hours=1)), \
         mock.patch.object(watchdog, "_now_utc", return_value=_now()):
        assert watchdog._check_service_heartbeats(_hb_cfg()) == []


def test_stale_heartbeat_flags():
    with mock.patch.object(watchdog, "_heartbeat_ts", return_value=_now() - timedelta(hours=7)), \
         mock.patch.object(watchdog, "_now_utc", return_value=_now()):
        out = watchdog._check_service_heartbeats(_hb_cfg())
        assert [a.fingerprint for a in out] == ["service-heartbeat-stale-sonar_inbox"]


def test_missing_heartbeat_flags_never_ran():
    with mock.patch.object(watchdog, "_heartbeat_ts", return_value=None):
        out = watchdog._check_service_heartbeats(_hb_cfg())
        assert [a.fingerprint for a in out] == ["service-heartbeat-missing-sonar_inbox"]


def test_heartbeat_db_error_is_skip_not_alarm():
    with mock.patch.object(watchdog, "_heartbeat_ts", side_effect=RuntimeError("db down")):
        assert watchdog._check_service_heartbeats(_hb_cfg()) == []


# ---- alert-rail freshness ---------------------------------------------------

def _rail_cfg(hours=6):
    return {"alert_rail": {"repo": "salesdroid/avo-telemetry",
                           "workflows": ["watchdog.yml"], "max_age_hours": hours}}


def _runs_response(created_at):
    resp = mock.Mock()
    resp.ok = True
    resp.json.return_value = {"workflow_runs": [{"created_at": created_at}]}
    return resp


def test_fresh_rail_run_no_anomaly():
    with mock.patch.object(watchdog.requests, "get",
                           return_value=_runs_response("2026-08-05T16:00:00Z")), \
         mock.patch.object(watchdog, "_now_utc", return_value=_now()):
        assert watchdog._check_alert_rail(_rail_cfg()) == []


def test_stale_rail_run_flags_critical():
    with mock.patch.object(watchdog.requests, "get",
                           return_value=_runs_response("2026-08-04T16:00:00Z")), \
         mock.patch.object(watchdog, "_now_utc", return_value=_now()):
        out = watchdog._check_alert_rail(_rail_cfg())
        assert [a.fingerprint for a in out] == ["alert-rail-stale-watchdog.yml"]
        assert out[0].severity == "critical"


def test_rail_no_successful_runs_flags():
    resp = mock.Mock(); resp.ok = True
    resp.json.return_value = {"workflow_runs": []}
    with mock.patch.object(watchdog.requests, "get", return_value=resp):
        out = watchdog._check_alert_rail(_rail_cfg())
        assert [a.fingerprint for a in out] == ["alert-rail-stale-watchdog.yml"]


def test_rail_api_unreachable_is_skip_not_alarm():
    with mock.patch.object(watchdog.requests, "get",
                           side_effect=watchdog.requests.ConnectionError("no net")):
        assert watchdog._check_alert_rail(_rail_cfg()) == []


def test_rail_disabled_probes_nothing():
    with mock.patch.object(watchdog.requests, "get") as seam:
        assert watchdog._check_alert_rail({"alert_rail": {"workflows": [], "max_age_hours": 6}}) == []
        assert watchdog._check_alert_rail(_rail_cfg(0)) == []
        seam.assert_not_called()


def test_run_hourly_emails_direct_only_for_new_rail_anomalies():
    rail = watchdog.Anomaly("alert-rail-stale-watchdog.yml", "rail dead", "critical")
    blog = watchdog.Anomaly("blog-stale-avi", "stale", "warn")
    with mock.patch.object(watchdog, "run_once", return_value=([rail, blog], [rail, blog])), \
         mock.patch.object(watchdog, "_email_alert_direct") as sent:
        watchdog.run_hourly()
        sent.assert_called_once_with([rail])
    # already-known rail anomaly (not new) must NOT re-email every hour
    with mock.patch.object(watchdog, "run_once", return_value=([rail], [])), \
         mock.patch.object(watchdog, "_email_alert_direct") as sent:
        watchdog.run_hourly()
        sent.assert_not_called()


# ---- acks + runbooks --------------------------------------------------------

def test_ack_map_honors_until_dates():
    cfg = {"acknowledged": [
        {"fingerprint": "emails-sent-zero", "until": "2099-01-01", "reason": "shadow mode"},
        {"fingerprint": "blog-stale-avi", "until": "2020-01-01", "reason": "expired"},
    ]}
    acks = watchdog._ack_map(cfg)
    assert "emails-sent-zero" in acks and acks["emails-sent-zero"]["reason"] == "shadow mode"
    assert "blog-stale-avi" not in acks  # past its until date -> resurfaces


def test_split_acked_partitions_and_attaches_runbooks():
    rows = [{"fingerprint": "emails-sent-zero", "human": "0 sent", "severity": "warn"},
            {"fingerprint": "blog-stale-avi", "human": "stale", "severity": "warn"}]
    acks = {"emails-sent-zero": {"until": "2099-01-01", "reason": "shadow mode"}}
    with mock.patch.object(watchdog, "_ack_map", return_value=acks):
        active, acked = watchdog._split_acked(rows)
    assert [a["fingerprint"] for a in active] == ["blog-stale-avi"]
    assert [a["fingerprint"] for a in acked] == ["emails-sent-zero"]
    assert acked[0]["reason"] == "shadow mode"
    # every anomaly ships with a proposed fix, not just a symptom
    assert active[0]["runbook"] and acked[0]["runbook"]


def test_every_fingerprint_class_has_a_runbook():
    for fp in ["site-network-https://x", "site-http-https://x-503", "telemetry-stale",
               "blog-stale-avi", "blog-404-avi", "blog-freshness-unknown-avi",
               "slipstream-queue-exhausted-avi", "weekly-social-stale",
               "monitor-stale-tp_daily", "monitor-freshness-unknown-tp_daily",
               "media-worker-down", "emails-sent-zero", "env-mislabelled",
               "postal-inbox-reauth-avi", "service-heartbeat-stale-sonar_inbox",
               "alert-rail-stale-watchdog.yml"]:
        assert watchdog._runbook(fp), f"no runbook for {fp}"


def test_current_state_json_serves_swept_at_and_acked():
    rows = [("emails-sent-zero", "0 sent", "warn"), ("blog-stale-avi", "stale", "warn")]
    swept = datetime(2026, 8, 5, 17, 0, tzinfo=timezone.utc)
    acks = {"emails-sent-zero": {"until": "2099-01-01", "reason": "shadow mode"}}
    with mock.patch("services.database.fetch_all", return_value=rows), \
         mock.patch.object(watchdog, "_heartbeat_ts", return_value=swept), \
         mock.patch.object(watchdog, "_ack_map", return_value=acks):
        js = watchdog.current_state_json()
    assert js["ok"] is True
    assert js["swept_at"] == swept.isoformat()
    assert [a["fingerprint"] for a in js["active_anomalies"]] == ["blog-stale-avi"]
    assert [a["fingerprint"] for a in js["acknowledged"]] == ["emails-sent-zero"]


def test_new_checks_registered():
    assert watchdog._check_service_heartbeats in watchdog._CHECKS
    assert watchdog._check_alert_rail in watchdog._CHECKS
