"""SP5 (AIPG no-call-close): honest evidence only, fail-closed, GHL-backed."""
from datetime import datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from services import sdr_aipg_no_call_close as A

_CT = ZoneInfo("America/Chicago")
_TUE_10AM = datetime(2026, 8, 11, 10, 0, tzinfo=_CT)

_MISSED_CALL_REVIEW = "Called three times, no one ever answered. Left a voicemail, never called back."
_NORMAL_REVIEW = "Great service, fixed my sink fast, very professional team."


def test_find_missed_call_evidence_requires_a_real_quote():
    assert A.find_missed_call_evidence([_NORMAL_REVIEW, _MISSED_CALL_REVIEW]) == _MISSED_CALL_REVIEW


def test_no_matching_review_means_no_evidence_never_fabricated():
    assert A.find_missed_call_evidence([_NORMAL_REVIEW, "Loved it, five stars!"]) is None


def test_compose_validate_quote_appears_verbatim():
    s, b = A.compose("Ben's Plumbing", "bensplumbing.com", _MISSED_CALL_REVIEW)
    assert A.validate(s, b, name="Ben's Plumbing", domain="bensplumbing.com",
                      quote=_MISSED_CALL_REVIEW) is None
    assert _MISSED_CALL_REVIEW in b
    assert "—" not in b
    assert "$" not in b and "price" not in b.lower()


def test_validator_catches_copy_drift():
    s, b = A.compose("Ben's Plumbing", "bensplumbing.com", _MISSED_CALL_REVIEW)
    assert A.validate(s, b + " call now for a discount!", name="Ben's Plumbing",
                      domain="bensplumbing.com", quote=_MISSED_CALL_REVIEW) == "copy_drift"


def test_franchise_and_websiteless_results_dropped(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    payload = {"places": [
        {"displayName": {"text": "No Site Plumbing"}},
        {"displayName": {"text": "Keller Williams Realty Dallas"}, "websiteUri": "https://kwdallas.com"},
        {"displayName": {"text": "Real Plumbing Co"}, "websiteUri": "https://real-plumbing.com",
         "reviews": [{"text": {"text": _MISSED_CALL_REVIEW}}], "nationalPhoneNumber": "(817) 555-0100"},
    ]}
    with mock.patch.object(A.requests, "post") as m:
        m.return_value.raise_for_status = lambda: None
        m.return_value.json = lambda: payload
        results = A.search_places_with_reviews("plumber in Dallas TX")
    names = {r["name"] for r in results}
    assert names == {"Real Plumbing Co"}
    assert results[0]["reviews"] == [_MISSED_CALL_REVIEW]


def _wire(monkeypatch, *, places=None, email="info@real-plumbing.com", contact=None,
         suppressed=False, scrutineer_block=False, sends_today=0):
    monkeypatch.setattr(A, "_SEGMENT_QUERIES", {"plumbing_operator": "plumber"})
    default_places = [{"name": "Real Plumbing Co", "domain": "real-plumbing.com",
                       "phone": "(817) 555-0100", "reviews": [_MISSED_CALL_REVIEW]}]
    monkeypatch.setattr(A, "search_places_with_reviews",
                        lambda q, limit=5: places if places is not None else default_places)
    monkeypatch.setattr(A, "_published_email", lambda d: email)
    monkeypatch.setattr(A, "_suppressed", lambda e, b: suppressed)
    monkeypatch.setattr(A, "_known_contact", lambda e: contact)
    monkeypatch.setattr(A, "_scrutineer",
                        lambda *a, **k: (scrutineer_block, "generic" if scrutineer_block else "ok"))
    monkeypatch.setattr(A, "_sends_today_safe", lambda: sends_today)
    monkeypatch.setattr(A, "_mark_touched", lambda *a, **k: None)


def test_dry_run_full_evaluation_writes_and_sends_nothing(monkeypatch):
    _wire(monkeypatch)
    out = A.run_no_call_close(commit=False, now=_TUE_10AM, cities=["Dallas, Texas"])
    assert out["sent"] == 0
    assert out["considered"] == 1
    assert "WOULD SEND to info@real-plumbing.com" in out["digest"]


def test_no_evidence_means_no_candidate_not_an_exception(monkeypatch):
    _wire(monkeypatch, places=[{"name": "Clean Co", "domain": "clean.com",
                                "phone": "", "reviews": [_NORMAL_REVIEW]}])
    out = A.run_no_call_close(commit=False, now=_TUE_10AM, cities=["Dallas, Texas"])
    assert out["considered"] == 0
    assert out["exceptions"] == 0
    assert out["found"] == 1


def test_kill_switch_blocks_commit_sends(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.delenv("SDR_FIRST_TOUCH_ENABLED", raising=False)
    sent = []
    monkeypatch.setattr("tools.ghl.send_email", lambda **k: sent.append(k))
    out = A.run_no_call_close(commit=True, now=_TUE_10AM, cities=["Dallas, Texas"])
    assert sent == []
    assert "kill_switch_off" in out["digest"]


def test_commit_creates_contact_sends_and_audits(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    created, sent, audited, marked = [], [], [], []
    monkeypatch.setattr("tools.ghl.create_contact",
                        lambda **k: created.append(k) or {"id": "ghl-contact-1"})
    monkeypatch.setattr("tools.ghl.send_email", lambda **k: sent.append(k) or {"id": "msg-1"})
    monkeypatch.setattr("services.brand_send_audit.record", lambda **k: audited.append(k))
    monkeypatch.setattr(A, "_mark_touched", lambda cid, q: marked.append(cid))
    out = A.run_no_call_close(commit=True, now=_TUE_10AM, cities=["Dallas, Texas"])
    assert out["sent"] == 1
    assert len(created) == 1 and created[0]["business_key"] == "aiphoneguy"
    assert len(sent) == 1 and sent[0]["contact_id"] == "ghl-contact-1"
    assert len(audited) == 1
    assert audited[0]["seat"] == "sdr_aipg_no_call_close"
    assert audited[0]["outcome"] == "sent"
    assert marked == ["ghl-contact-1"]


def test_already_touched_contact_is_skipped(monkeypatch):
    _wire(monkeypatch, contact={"id": "c1", "tags": [A._TOUCHED_TAG]})
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    sent = []
    monkeypatch.setattr("tools.ghl.send_email", lambda **k: sent.append(k))
    out = A.run_no_call_close(commit=True, now=_TUE_10AM, cities=["Dallas, Texas"])
    assert sent == []
    assert "already touched" in out["digest"]


def test_every_guardrail_dies_as_exception_never_a_send(monkeypatch):
    cases = [
        dict(email=None),
        dict(suppressed=True),
        dict(scrutineer_block=True),
        dict(sends_today=A.DAILY_CAP),
    ]
    for kw in cases:
        _wire(monkeypatch, **kw)
        monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
        sent = []
        monkeypatch.setattr("tools.ghl.send_email", lambda **k: sent.append(k))
        monkeypatch.setattr("tools.ghl.create_contact", lambda **k: {"id": "c1"})
        monkeypatch.setattr("services.brand_send_audit.record", lambda **k: None)
        out = A.run_no_call_close(commit=True, now=_TUE_10AM, cities=["Dallas, Texas"])
        assert sent == [], f"guardrail leaked a send for {kw}"
        assert out["sent"] == 0


def test_cap_check_fails_closed_on_db_error(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(A, "_sends_today", boom)
    assert A._sends_today_safe() == A.DAILY_CAP


def test_missing_places_key_raises_clean_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    try:
        A._places_key()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "GOOGLE_PLACES_API_KEY" in str(e)
