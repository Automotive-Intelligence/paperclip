"""Lead capture fail-closed contract (funnel standard item 5)."""
from unittest import mock

from services import lead_capture as LC


def test_ok_false_when_no_human_alerted():
    # No email, no issue -> receipt-only -> MUST fail closed (ok False).
    with mock.patch.object(LC, "_alert_email", return_value=False), \
         mock.patch.object(LC, "_alert_issue", return_value=False):
        r = LC.capture({"brand": "aipg", "name": "Test", "phone": "555"})
    assert r["ok"] is False
    assert r["alerted"] is False
    assert r["via"] == "receipt-only"


def test_ok_true_only_when_emailed():
    with mock.patch.object(LC, "_alert_email", return_value=True), \
         mock.patch.object(LC, "_alert_issue", return_value=False):
        r = LC.capture({"brand": "aipg", "name": "Test", "phone": "555"})
    assert r["ok"] is True and r["via"] == "email"


def test_ok_true_when_issue_backup_fires():
    with mock.patch.object(LC, "_alert_email", return_value=False), \
         mock.patch.object(LC, "_alert_issue", return_value=True):
        r = LC.capture({"brand": "aipg", "name": "Test"})
    assert r["ok"] is True and r["via"] == "issue"


def test_default_alert_to_is_not_the_dead_inbox():
    assert "worshipdigital.co" not in LC.ALERT_TO
    assert LC.ALERT_TO == "michael@automotiveintelligence.io"
