from unittest import mock

from services import watchdog


def test_current_state_json_reads_table():
    rows = [("blog-stale-b", "b stale", "warn")]
    with mock.patch("services.database.fetch_all", return_value=rows):
        js = watchdog.current_state_json()
    assert js["ok"] is True
    assert js["active_anomalies"][0]["fingerprint"] == "blog-stale-b"
    assert js["active_anomalies"][0]["severity"] == "warn"


def test_current_state_json_db_error_is_soft():
    def _boom(*a, **k):
        raise RuntimeError("db down")

    with mock.patch("services.database.fetch_all", side_effect=_boom):
        js = watchdog.current_state_json()
    assert js["ok"] is False
    assert js["active_anomalies"] == []
