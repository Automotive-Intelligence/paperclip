"""instantly_reply_sync: replies out of Instantly's paywalled Unibox into the
brand's Twenty, through the existing idempotent intent_inbound pipeline.

Contract under test: dry-run writes NOTHING; commit feeds handle_event with the
Instantly message id as the idempotency key + the fail-closed label as subtype;
noise (bounce / autoreply / negative) never syncs; a dedupe from the pipeline
suppresses the note; one bad replier never kills the sweep.
"""
from unittest import mock

from services import instantly_reply_sync as S

_MSG = {
    "id": "msg-123",
    "subject": "Re: still waiting on a call back",
    "from_address_email": "rparrish@chuckhutton.com",
    "timestamp_email": "2026-07-15T14:27:26.000Z",
    "body": {"text": "Can I help you?"},
}

_NDR = {
    "id": "msg-999",
    "subject": "Re: a month of messages, no callback",
    "from_address_email": "tknutson@carhop.com",
    "timestamp_email": "2026-08-03T13:35:11.000Z",
    "body": {"text": "This is an automated message from mail service of "
                     "tknutson@carhop.com Message not delivered 550 5.4.1 "
                     "Recipient address rejected"},
}


def _patch_fetch(monkeypatch, repliers, received_by_email):
    monkeypatch.setenv("INSTANTLY_API_KEY_AVI", "k")
    monkeypatch.delenv("INSTANTLY_API_KEY_BOOKD", raising=False)
    monkeypatch.setattr(S, "_fetch_repliers", lambda H: repliers)
    monkeypatch.setattr(S, "_fetch_received",
                        lambda H, em: received_by_email.get(em, []))


def test_dry_run_reads_classifies_and_writes_nothing(monkeypatch):
    _patch_fetch(monkeypatch, [("Dealer #1", "rparrish@chuckhutton.com")],
                 {"rparrish@chuckhutton.com": [_MSG]})
    called = []
    monkeypatch.setattr(S, "_sync_one", lambda p, l: called.append(p) or ("synced", "x"))
    out = S.run_reply_sync(commit=False)
    assert called == []                      # nothing written
    assert out["replies_seen"] == 1
    assert out["synced"] == 0
    assert "[positive] would sync" in out["digest"]


def test_commit_feeds_pipeline_with_message_id_key_and_label(monkeypatch):
    _patch_fetch(monkeypatch, [("Dealer #1", "rparrish@chuckhutton.com")],
                 {"rparrish@chuckhutton.com": [_MSG]})
    fed = []

    def fake_handle(payload):
        fed.append(payload)
        return {"ok": True, "audit": {"deduped": False},
                "twenty": {"twenty_id": "p1", "business_key": "autointelligence"}}

    notes = []
    with mock.patch("services.intent_inbound.handle_event", fake_handle), \
         mock.patch("tools.twenty.create_note_for_person",
                    lambda **k: notes.append(k) or "n1"):
        out = S.run_reply_sync(commit=True)

    assert out["synced"] == 1
    p = fed[0]
    assert p["idempotency_key"] == "instantly:msg-123"
    assert p["subtype"] == "positive"
    assert p["person_ref"] == {"email": "rparrish@chuckhutton.com"}
    assert p["raw_body"]["reply_text"].startswith("Can I help you?")
    assert len(notes) == 1
    assert notes[0]["person_id"] == "p1"
    assert "POSITIVE" in notes[0]["title"]


def test_noise_is_never_synced(monkeypatch):
    _patch_fetch(monkeypatch, [("Dealer #1", "tknutson@carhop.com")],
                 {"tknutson@carhop.com": [_NDR]})
    fed = []
    with mock.patch("services.intent_inbound.handle_event",
                    lambda p: fed.append(p)):
        out = S.run_reply_sync(commit=True)
    assert fed == []
    assert out["skipped_noise"] == 1
    assert out["synced"] == 0
    assert "[bounce] noise, not synced" in out["digest"]


def test_pipeline_dedupe_suppresses_the_note(monkeypatch):
    _patch_fetch(monkeypatch, [("Dealer #1", "rparrish@chuckhutton.com")],
                 {"rparrish@chuckhutton.com": [_MSG]})
    notes = []
    with mock.patch("services.intent_inbound.handle_event",
                    lambda p: {"ok": True, "audit": {"deduped": True}, "twenty": {}}), \
         mock.patch("tools.twenty.create_note_for_person",
                    lambda **k: notes.append(k)):
        out = S.run_reply_sync(commit=True)
    assert out["deduped"] == 1 and out["synced"] == 0
    assert notes == []


def test_one_bad_replier_never_kills_the_sweep(monkeypatch):
    def flaky_received(H, em):
        if em == "boom@x.com":
            raise RuntimeError("api reset")
        return [_MSG]
    monkeypatch.setenv("INSTANTLY_API_KEY_AVI", "k")
    monkeypatch.delenv("INSTANTLY_API_KEY_BOOKD", raising=False)
    monkeypatch.setattr(S, "_fetch_repliers",
                        lambda H: [("c", "boom@x.com"), ("c", "rparrish@chuckhutton.com")])
    monkeypatch.setattr(S, "_fetch_received", flaky_received)
    out = S.run_reply_sync(commit=False)
    assert out["errors"] == 1
    assert out["replies_seen"] == 1          # the good replier still processed
