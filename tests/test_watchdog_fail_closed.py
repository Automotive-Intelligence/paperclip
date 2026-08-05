"""Fail-closed hardening: probe retries (transients never email) and the postal
expected-accounts check (a wiped token table must never read as healthy)."""
from unittest import mock

from services import watchdog


class _Resp:
    def __init__(self, code):
        self.status_code = code


# ---- _get_with_retries ------------------------------------------------------

def test_transient_429_then_recovery_is_not_a_failure():
    """The 2026-08-05 P&P case: one 429 probe cost two emails. A transient must
    be retried away, not alerted."""
    seq = iter([_Resp(429), _Resp(200)])
    with mock.patch.object(watchdog.requests, "get", side_effect=lambda *a, **k: next(seq)), \
         mock.patch.object(watchdog, "_sleep") as slept:
        r = watchdog._get_with_retries("https://x")
    assert r.status_code == 200
    slept.assert_called_once()


def test_persistent_failure_returns_final_response():
    with mock.patch.object(watchdog.requests, "get", return_value=_Resp(503)), \
         mock.patch.object(watchdog, "_sleep"):
        assert watchdog._get_with_retries("https://x").status_code == 503


def test_exception_then_success_recovers():
    seq = [watchdog.requests.ConnectionError("blip"), _Resp(200)]

    def _get(*a, **k):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    with mock.patch.object(watchdog.requests, "get", side_effect=_get), \
         mock.patch.object(watchdog, "_sleep"):
        assert watchdog._get_with_retries("https://x").status_code == 200


def test_persistent_exception_raises():
    with mock.patch.object(watchdog.requests, "get",
                           side_effect=watchdog.requests.ConnectionError("down")), \
         mock.patch.object(watchdog, "_sleep"):
        try:
            watchdog._get_with_retries("https://x")
            assert False, "should have raised"
        except watchdog.requests.ConnectionError:
            pass


def test_success_first_try_never_sleeps():
    with mock.patch.object(watchdog.requests, "get", return_value=_Resp(200)), \
         mock.patch.object(watchdog, "_sleep") as slept:
        watchdog._get_with_retries("https://x")
        slept.assert_not_called()


def test_brand_sites_alert_only_after_retries_exhausted():
    cfg = {"site_urls": ["https://flappy.example"]}
    seq = iter([_Resp(429), _Resp(200)])
    with mock.patch.object(watchdog, "load_watchdog_config", return_value=cfg), \
         mock.patch.object(watchdog.requests, "get", side_effect=lambda *a, **k: next(seq)), \
         mock.patch.object(watchdog, "_sleep"):
        assert watchdog._check_brand_sites() == []


# ---- postal fail-closed -----------------------------------------------------

def _pa_cfg(expected):
    return {"postal_auth": {"enabled": True, "expected_accounts": expected}}


_TWO_ACTIVE = [{"account_label": "avi", "email": "a@a", "status": "active"},
               {"account_label": "wd", "email": "w@w", "status": "active"}]


def test_all_expected_present_no_anomaly():
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=_TWO_ACTIVE):
        assert watchdog._check_postal_auth(_pa_cfg(["avi", "wd"])) == []


def test_wiped_table_fails_closed():
    """The fail-open case this exists for: source returns [] (wiped/migrated
    table) -- previously read as healthy, now every expected account alerts."""
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=[]):
        out = watchdog._check_postal_auth(_pa_cfg(["avi", "wd", "aipg"]))
        assert sorted(a.fingerprint for a in out) == [
            "postal-inbox-missing-aipg", "postal-inbox-missing-avi",
            "postal-inbox-missing-wd"]


def test_one_dropped_account_flags_just_that_one():
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=_TWO_ACTIVE):
        out = watchdog._check_postal_auth(_pa_cfg(["avi", "wd", "bookd"]))
        assert [a.fingerprint for a in out] == ["postal-inbox-missing-bookd"]


def test_source_exception_still_skips_not_alarms():
    """Raising source = infra down = uptime rail's job; absence-of-rows is the
    only fail-closed trigger."""
    with mock.patch.object(watchdog, "_postal_auth_accounts",
                           side_effect=RuntimeError("db down")):
        assert watchdog._check_postal_auth(_pa_cfg(["avi"])) == []


def test_missing_and_reauth_compose():
    stale = [{"account_label": "avi", "email": "a@a", "status": "needs_reauth"}]
    with mock.patch.object(watchdog, "_postal_auth_accounts", return_value=stale):
        out = watchdog._check_postal_auth(_pa_cfg(["avi", "wd"]))
        assert sorted(a.fingerprint for a in out) == [
            "postal-inbox-missing-wd", "postal-inbox-reauth-avi"]


def test_missing_class_has_runbook():
    assert watchdog._runbook("postal-inbox-missing-avi")
