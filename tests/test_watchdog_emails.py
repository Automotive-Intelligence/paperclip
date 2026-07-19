from unittest import mock

from services import watchdog


def _cfg():
    return {"emails_sent": {"window_days": 7, "min_prospects_for_alert": 25}}


def test_zero_emails_with_pipeline_flags():
    summ = {"emails_sent": 0, "prospects_created": 40}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        out = watchdog._check_emails_sent(_cfg())
        assert any(a.fingerprint == "emails-sent-zero" for a in out)


def test_zero_emails_but_empty_pipeline_no_flag():
    summ = {"emails_sent": 0, "prospects_created": 3}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        assert watchdog._check_emails_sent(_cfg()) == []


def test_emails_flowing_no_flag():
    summ = {"emails_sent": 12, "prospects_created": 40}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        assert watchdog._check_emails_sent(_cfg()) == []


def test_db_error_summary_no_flag():
    summ = {"error": "Database not available"}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        assert watchdog._check_emails_sent(_cfg()) == []
