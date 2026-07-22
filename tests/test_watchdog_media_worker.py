"""watchdog._check_media_worker_health: disabled, healthy, down, unhealthy, registered."""
import requests
from unittest import mock

from services import watchdog


def _cfg(url="", severity=None):
    mw = {}
    if url:
        mw["health_url"] = url
    if severity:
        mw["severity"] = severity
    return {"media_worker": mw}


def test_disabled_when_no_url_probes_nothing():
    with mock.patch.object(watchdog, "_http_status") as seam:
        assert watchdog._check_media_worker_health(_cfg("")) == []
        seam.assert_not_called()


def test_healthy_200_no_anomaly():
    with mock.patch.object(watchdog, "_http_status", return_value=200):
        assert watchdog._check_media_worker_health(_cfg("https://mw/health")) == []


def test_network_error_flags_down_critical():
    with mock.patch.object(watchdog, "_http_status",
                           side_effect=requests.ConnectionError("refused")):
        out = watchdog._check_media_worker_health(_cfg("https://mw/health"))
        assert len(out) == 1
        assert out[0].fingerprint == "media-worker-down"
        assert out[0].severity == "critical"  # default


def test_non_200_flags_http_code():
    with mock.patch.object(watchdog, "_http_status", return_value=503):
        out = watchdog._check_media_worker_health(_cfg("https://mw/health"))
        assert len(out) == 1
        assert out[0].fingerprint == "media-worker-http-503"


def test_severity_override_from_config():
    with mock.patch.object(watchdog, "_http_status", return_value=500):
        out = watchdog._check_media_worker_health(_cfg("https://mw/health", severity="warn"))
        assert out[0].severity == "warn"


def test_registered_in_checks():
    assert watchdog._check_media_worker_health in watchdog._CHECKS
