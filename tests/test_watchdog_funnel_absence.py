"""watchdog._check_lead_funnel_absence: funnel standard #17 (absent_over_time)."""
from unittest import mock

from services import watchdog


def _cfg(max_h=3):
    return {"monitors": {"lead_canary_max_age_hours": max_h}}


def _patch_latest(latest):
    return mock.patch("services.lead_canary.latest_canary", return_value=latest)


def test_disabled_when_zero():
    with _patch_latest(None):
        assert watchdog._check_lead_funnel_absence({"monitors": {"lead_canary_max_age_hours": 0}}) == []


def test_never_ran_is_warn_not_critical():
    with _patch_latest(None):
        out = watchdog._check_lead_funnel_absence(_cfg())
    assert len(out) == 1 and out[0].severity == "warn"
    assert out[0].fingerprint == "lead-canary-never-ran"


def test_fresh_green_no_anomaly():
    with _patch_latest({"responded": True, "age_seconds": 1800, "target": "form"}):
        assert watchdog._check_lead_funnel_absence(_cfg()) == []


def test_silent_canary_is_critical():
    # last run 5h ago (> 3h) -> the verifier itself went quiet.
    with _patch_latest({"responded": True, "age_seconds": 5 * 3600, "target": "form"}):
        out = watchdog._check_lead_funnel_absence(_cfg())
    assert any(a.fingerprint == "lead-canary-silent" and a.severity == "critical" for a in out)


def test_red_canary_is_critical():
    with _patch_latest({"responded": False, "age_seconds": 600, "target": "form"}):
        out = watchdog._check_lead_funnel_absence(_cfg())
    assert any(a.fingerprint == "lead-canary-red" and a.severity == "critical" for a in out)


def test_registered_in_checks():
    assert watchdog._check_lead_funnel_absence in watchdog._CHECKS
