"""Funnel standard #7: reconciliation diffs system-of-record vs CRM, fails closed."""
from unittest import mock

from services import lead_reconcile as LR


def test_agree_is_green_no_alert():
    with mock.patch.object(LR, "recent_count", return_value=3), \
         mock.patch.object(LR, "_ghl_recent_count", return_value=3), \
         mock.patch.object(LR, "_alert") as alert:
        r = LR.reconcile("aipg", 24)
    assert r["ok"] is True and r["delta"] == 0 and not alert.called


def test_delta_alerts_with_direction():
    # store 5, CRM 3 -> +2 stored-but-not-in-CRM (dead letters)
    with mock.patch.object(LR, "recent_count", return_value=5), \
         mock.patch.object(LR, "_ghl_recent_count", return_value=3), \
         mock.patch.object(LR, "_alert", return_value=True) as alert:
        r = LR.reconcile("aipg", 24)
    assert r["ok"] is False and r["delta"] == 2 and alert.called
    # store 1, CRM 4 -> -3 in-CRM-but-not-stored (a bypass path)
    with mock.patch.object(LR, "recent_count", return_value=1), \
         mock.patch.object(LR, "_ghl_recent_count", return_value=4), \
         mock.patch.object(LR, "_alert", return_value=True) as alert2:
        r2 = LR.reconcile("aipg", 24)
    assert r2["delta"] == -3 and alert2.called


def test_unreadable_crm_fails_closed_and_alerts():
    # CRM read returns None -> we cannot prove the funnel is whole -> alert, ok=False.
    with mock.patch.object(LR, "recent_count", return_value=2), \
         mock.patch.object(LR, "_ghl_recent_count", return_value=None), \
         mock.patch.object(LR, "_alert", return_value=True) as alert:
        r = LR.reconcile("aipg", 24)
    assert r["ok"] is False and r["crm"] is None and alert.called


def test_ghl_count_none_when_creds_missing():
    with mock.patch.dict("os.environ", {"GHL_API_KEY": "", "GHL_LOCATION_ID": ""}):
        assert LR._ghl_recent_count(24) is None
