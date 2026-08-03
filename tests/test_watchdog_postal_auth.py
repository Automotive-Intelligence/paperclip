"""watchdog._check_postal_auth: the auth-state guard for the inbound Postal/Gmail
rail. Covers the three contract cases from the 2026-07-01 silent-blackout postmortem
(all 6 inboxes dark for a MONTH, unwatched): all-active -> 0 anomalies; one
needs_reauth -> exactly 1 anomaly with the right fingerprint; status source
raises -> 0 anomalies / no crash (fail-safe, never a false re-auth). Plus the
config gate and registry wiring, mirroring test_watchdog_media_worker.py."""
from unittest import mock

from services import watchdog


def _cfg(enabled=True, severity=None):
    pa = {"enabled": enabled}
    if severity:
        pa["severity"] = severity
    return {"postal_auth": pa}


_ALL_ACTIVE = [
    {"account_label": "avi", "email": "michael@automotiveintelligence.io",
     "status": "active", "last_reauth_at": "2026-07-31T00:00:00Z"},
    {"account_label": "wd", "email": "hello@worshipdigital.co",
     "status": "active", "last_reauth_at": "2026-07-31T00:00:00Z"},
]

_ONE_STALE = [
    {"account_label": "avi", "email": "michael@automotiveintelligence.io",
     "status": "active", "last_reauth_at": "2026-07-31T00:00:00Z"},
    {"account_label": "aipg", "email": "hello@theaiphoneguy.com",
     "status": "needs_reauth", "last_reauth_at": None},
]


def test_all_active_no_anomaly():
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=_ALL_ACTIVE):
        assert watchdog._check_postal_auth(_cfg()) == []


def test_one_needs_reauth_flags_that_account():
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=_ONE_STALE):
        out = watchdog._check_postal_auth(_cfg())
    assert len(out) == 1                                    # only the dark inbox
    assert out[0].fingerprint == "postal-inbox-reauth-aipg"
    assert out[0].severity == "warn"
    assert "aipg" in out[0].human and "theaiphoneguy.com" in out[0].human


def test_source_raises_is_fail_safe_no_anomaly():
    """A raising/unreachable status source is logged + skipped, never a false
    'needs re-auth' (a true service-down is paperclip_uptime.yml's job)."""
    with mock.patch.object(watchdog, "_postal_auth_accounts",
                           side_effect=RuntimeError("postal_tokens unreachable")):
        assert watchdog._check_postal_auth(_cfg()) == []


def test_disabled_when_enabled_false_probes_nothing():
    with mock.patch.object(watchdog, "_postal_auth_accounts") as seam:
        assert watchdog._check_postal_auth(_cfg(enabled=False)) == []
        seam.assert_not_called()


def test_missing_config_key_no_ops():
    """No postal_auth block at all -> treated as disabled, probes nothing."""
    with mock.patch.object(watchdog, "_postal_auth_accounts") as seam:
        assert watchdog._check_postal_auth({}) == []
        seam.assert_not_called()


def test_severity_override_from_config():
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=_ONE_STALE):
        out = watchdog._check_postal_auth(_cfg(severity="critical"))
    assert out[0].severity == "critical"


def test_registered_in_checks():
    assert watchdog._check_postal_auth in watchdog._CHECKS
