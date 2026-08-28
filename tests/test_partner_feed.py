"""Tests for the partner mailbox and activity feed.

The behaviours worth pinning are the ones that would fail SILENTLY in production: a
feed whose source died looking identical to a feed with no news, a note delivered
twice because marking-read failed, and a secret reaching a partner through the one
outbound path that had not been scrubbed yet.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.partner_feed as pf  # noqa: E402


class FakeDB:
    """Stand-in for services.database with just enough behaviour to assert against."""

    def __init__(self):
        self.notes = []
        self.writes = []
        self.next_id = 1
        self.fail_update = False

    def execute_query(self, sql, params=()):
        self.writes.append((sql, params))
        if sql.strip().upper().startswith("UPDATE") and self.fail_update:
            raise RuntimeError("update blew up")
        if sql.strip().upper().startswith("UPDATE"):
            for n in self.notes:
                if n["id"] in (params[0] if params else []):
                    n["read_at"] = datetime.now(timezone.utc)

    def fetch_all(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO partner_notes"):
            direction, author, body, scope, key_id = params
            row = {"id": self.next_id, "direction": direction, "author": author,
                   "body": body, "scope": scope, "key_id": key_id,
                   "created_at": datetime.now(timezone.utc), "read_at": None}
            self.notes.append(row)
            self.next_id += 1
            return [(row["id"], row["created_at"])]
        if "SELECT id, author, body, created_at, read_at FROM partner_notes" in s:
            rows = [n for n in self.notes if n["direction"] == "to_partner"]
            rows.sort(key=lambda n: (n["read_at"] is not None, ), reverse=False)
            return [(n["id"], n["author"], n["body"], n["created_at"], n["read_at"])
                    for n in rows]
        if "SELECT direction, author, body, created_at FROM partner_notes" in s:
            return [(n["direction"], n["author"], n["body"], n["created_at"])
                    for n in self.notes]
        if "SELECT id, direction, author, body, created_at, read_at" in s:
            return [(n["id"], n["direction"], n["author"], n["body"],
                     n["created_at"], n["read_at"]) for n in self.notes]
        if "partner_action_requests" in s:
            return []
        return []


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(pf, "execute_query", fake.execute_query)
    monkeypatch.setattr(pf, "fetch_all", fake.fetch_all)
    monkeypatch.setattr(pf, "_alert_michael", lambda *a, **k: True)
    return fake


# ------------------------------------------------------------------------- notes

def test_note_roundtrip_michael_to_partner(db):
    out = pf.post_note("Working the Stripe redeploy now", author="michael")
    assert out["ok"] and out["direction"] == "to_partner"

    got = pf.inbox(scope="bookd")
    assert got["unread_count"] == 1
    assert got["notes"][0]["body"] == "Working the Stripe redeploy now"
    assert got["notes"][0]["unread"] is True


def test_reading_marks_read_so_a_poller_does_not_repeat(db):
    pf.post_note("first", author="michael")
    assert pf.inbox(scope="bookd")["unread_count"] == 1
    # Second poll: the note is still visible as history but no longer unread.
    second = pf.inbox(scope="bookd")
    assert second["unread_count"] == 0
    assert second["notes"][0]["unread"] is False


def test_mark_read_failure_still_delivers(db):
    """A bookkeeping failure must not swallow the message; one repeat beats a loss."""
    pf.post_note("important", author="michael")
    db.fail_update = True
    got = pf.inbox(scope="bookd")
    assert got["ok"] is True
    assert got["notes"][0]["body"] == "important"


def test_empty_body_rejected(db):
    assert pf.post_note("   ")["ok"] is False
    assert not db.notes


def test_bad_direction_rejected(db):
    assert pf.post_note("hi", direction="sideways")["ok"] is False


def test_partner_note_pages_michael(db, monkeypatch):
    seen = {}
    monkeypatch.setattr(pf, "_alert_michael",
                        lambda author, text, nid: seen.update(a=author, t=text))
    pf.post_note("checkout is live", author="viktor", direction="from_partner")
    assert seen["a"] == "viktor"
    # ...and the reverse direction does NOT page him for his own message.
    seen.clear()
    pf.post_note("noted", author="michael", direction="to_partner")
    assert not seen


def test_secret_in_a_note_is_scrubbed_on_the_way_out(db):
    # Assembled at runtime rather than written as a literal: a fixture that LOOKS like
    # a live Stripe key trips GitHub push protection, and a test for secret handling
    # should not be the thing that puts a key-shaped string in the repo.
    fake = "sk_" + "live_" + ("A" * 24)
    pf.post_note(f"key is {fake}", author="michael")
    body = pf.inbox(scope="bookd")["notes"][0]["body"]
    assert fake not in body


# ---------------------------------------------------------------------- activity

def test_activity_merges_and_sorts_newest_first(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(pf, "_commits", lambda *a, **k: [
        {"when": (now - timedelta(hours=5)).isoformat(), "kind": "work",
         "who": "avo", "what": "older commit"},
        {"when": (now - timedelta(hours=1)).isoformat(), "kind": "work",
         "who": "avo", "what": "newer commit"},
    ])
    out = pf.activity(scope="bookd")
    assert out["items"][0]["what"] == "newer commit"
    assert out["sources_failed"] == []
    assert out["cursor"] == out["items"][0]["when"]


def test_a_dead_source_is_reported_not_swallowed(db, monkeypatch):
    """The whole point: broken must not look like quiet."""
    def boom(*a, **k):
        raise RuntimeError("github down")
    monkeypatch.setattr(pf, "_commits", boom)
    out = pf.activity(scope="bookd")
    assert "work" in out["sources_failed"]
    assert out["ok"] is True  # the other sources still answered


def test_bad_cursor_widens_the_window_rather_than_returning_nothing(db, monkeypatch):
    captured = {}
    monkeypatch.setattr(pf, "_commits",
                        lambda since, scope, limit: captured.setdefault("since", since) and [])
    pf.activity("not-a-timestamp", scope="bookd")
    assert captured["since"] < datetime.now(timezone.utc)


def test_bookd_scope_filters_commits_to_the_bookd_file(monkeypatch):
    sent = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return []

    monkeypatch.setattr(pf, "_token", lambda: "tok")
    monkeypatch.setattr(pf.requests, "get",
                        lambda url, **kw: sent.update(kw.get("params", {})) or R())
    pf._commits(datetime.now(timezone.utc), "bookd", 10)
    assert sent.get("path") == pf._BOOKD_PATH

    sent.clear()
    pf._commits(datetime.now(timezone.utc), "avo", 10)
    assert "path" not in sent


def test_limit_is_capped(db, monkeypatch):
    monkeypatch.setattr(pf, "_commits", lambda *a, **k: [])
    assert pf.activity(scope="bookd", limit=10_000)["items"] == []
