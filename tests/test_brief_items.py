"""brief_items: dedupe by fingerprint, never raises on a dead DB, root-cause compare."""
from unittest import mock

from services import brief_items as BI


def test_add_returns_true_only_for_new_rows():
    calls = []

    def fake_fetch(sql, params=()):
        calls.append(params)
        return [(1,)] if len(calls) == 1 else []

    with mock.patch.object(BI, "execute_query"), \
         mock.patch.object(BI, "fetch_all", side_effect=fake_fetch):
        assert BI.add("sonar", "comment on IG", fingerprint="sonar:c1") is True
        assert BI.add("sonar", "comment on IG", fingerprint="sonar:c1") is False


def test_add_never_raises_when_db_is_down():
    with mock.patch.object(BI, "execute_query", side_effect=RuntimeError("db down")):
        assert BI.add("postal", "prospect", fingerprint="postal:x") is False


def test_add_rejects_empty_fingerprint():
    assert BI.add("postal", "prospect", fingerprint="") is False


def test_open_items_shapes_rows():
    row = (7, "sonar", "sonar:c1", "aipg", "t", "d", "http://x", "2026-11-06")
    with mock.patch.object(BI, "execute_query"), \
         mock.patch.object(BI, "fetch_all", return_value=[row]):
        items = BI.open_items(days=3, kinds=["sonar"])
    assert items == [{"id": 7, "kind": "sonar", "fingerprint": "sonar:c1", "brand": "aipg",
                      "title": "t", "detail": "d", "url": "http://x",
                      "created_at": "2026-11-06"}]


def test_same_root_cause():
    assert BI.same_root_cause("401 Client Error: Unauthorized for url: a", "401 Client Error: Unauthorized for url: a")
    assert not BI.same_root_cause(None, "anything")
    assert not BI.same_root_cause("gate failed twice", "401 Unauthorized")
