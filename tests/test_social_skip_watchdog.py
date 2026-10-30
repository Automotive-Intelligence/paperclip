"""The guardrail for the 7-week silent social outage.

127 rows reading {"action":"skipped","post_id":null} sat in the registry from
2026-09-07 to 2026-10-19 and nothing read them. These tests pin the two things
that make this check worth having: it fires on the real signature, and it does
NOT treat "I could not look" as "nothing is wrong".
"""
from __future__ import annotations

import base64
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import services.watchdog as W  # noqa: E402


def _registry(rows):
    body = "\n".join(json.dumps(r) for r in rows)
    return base64.b64encode(body.encode()).decode()


def _resp(monkeypatch, *, ok=True, status=200, content=None, raise_exc=False):
    class R:
        def __init__(self):
            self.ok = ok
            self.status_code = status
        def json(self):
            return {"content": content or ""}
    def get(*a, **k):
        if raise_exc:
            raise W.requests.RequestException("boom")
        return R()
    monkeypatch.setattr(W, "requests",
                        types.SimpleNamespace(get=get, RequestException=Exception))


def _now_iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_fires_on_a_recent_skip(monkeypatch):
    rows = [{"ts": _now_iso(1), "action": "skipped", "skip_reason": "media_unreachable",
             "business_key": "aiphoneguy", "post_id": None}]
    _resp(monkeypatch, content=_registry(rows))
    out = W._check_social_skips({"monitors": {"social_skip_window_days": 7}})
    assert [a.fingerprint for a in out] == ["social-posts-silently-skipped"]
    assert out[0].severity == "critical"
    assert "aiphoneguy" in out[0].human and "media_unreachable" in out[0].human


def test_a_null_post_id_counts_even_without_the_action_key(monkeypatch):
    """post_id null IS the signature; action is only how it happened to be logged."""
    rows = [{"ts": _now_iso(1), "brand": "autointelligence", "post_id": None}]
    _resp(monkeypatch, content=_registry(rows))
    out = W._check_social_skips({})
    assert out and out[0].fingerprint == "social-posts-silently-skipped"


def test_healthy_rows_are_quiet(monkeypatch):
    """Verified against the live registry: every successful row carries a post_id."""
    rows = [{"ts": _now_iso(1), "brand": "aipg", "post_id": "6a54079db5ababa5743cb4c0"},
            {"ts": _now_iso(2), "brand": "aipg", "post_id": "abc123"}]
    _resp(monkeypatch, content=_registry(rows))
    assert W._check_social_skips({}) == []


def test_old_skips_do_not_re_alarm_forever(monkeypatch):
    rows = [{"ts": _now_iso(400), "action": "skipped", "post_id": None, "brand": "aipg"}]
    _resp(monkeypatch, content=_registry(rows))
    assert W._check_social_skips({"monitors": {"social_skip_window_days": 7}}) == []


def test_unreadable_registry_warns_rather_than_passing_silently(monkeypatch):
    """The whole lesson: could-not-look must never look like nothing-wrong."""
    _resp(monkeypatch, ok=False, status=401)
    out = W._check_social_skips({})
    assert [a.fingerprint for a in out] == ["social-registry-unreadable"]
    assert "not the same as there being none" in out[0].human


def test_network_error_also_warns(monkeypatch):
    _resp(monkeypatch, raise_exc=True)
    out = W._check_social_skips({})
    assert out and out[0].fingerprint == "social-registry-unreadable"


def test_can_be_disabled(monkeypatch):
    _resp(monkeypatch, ok=False, status=500)
    assert W._check_social_skips({"monitors": {"social_skip_window_days": 0}}) == []


def test_is_registered():
    assert W._check_social_skips in W._CHECKS


def test_against_the_real_historical_outage(monkeypatch):
    """The actual 127 rows, replayed as if they were recent: must alarm."""
    reg = Path.home() / "avo-telemetry" / "social_registry.jsonl"
    if not reg.exists():
        pytest.skip("registry not present locally")
    rows = []
    for line in reg.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("action") == "skipped":
            r["ts"] = _now_iso(1)
            rows.append(r)
    assert len(rows) >= 100, "expected the historical skip rows"
    _resp(monkeypatch, content=_registry(rows))
    out = W._check_social_skips({})
    assert out[0].fingerprint == "social-posts-silently-skipped"
    assert "aiphoneguy" in out[0].human and "autointelligence" in out[0].human
