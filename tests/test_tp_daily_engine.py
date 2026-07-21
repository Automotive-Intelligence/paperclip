"""tp_daily_engine: block format, idempotent insert-transform, dry-run preview."""
from unittest import mock

from services import tp_daily_engine as T

STATE = """# Team Principal State
**Owner:** Team Principal
**Last updated:** 2026-07-19 (something)
**Status:** green

Some preamble.

## 🏁 TP daily -- 2026-07-19

old heartbeat
"""


def test_build_block_with_rows_has_table_and_decision():
    rows = [("AvI", "Dealer #1", 186, 60, 1, 1)]
    b = T.build_block(rows, [], "2026-07-20")
    assert "interested humans = 1" in b
    assert "| AvI | Dealer #1 |" in b
    assert "WORK THE 1 INTERESTED" in b
    assert "—" not in b   # no em-dashes


def test_build_block_no_rows_says_nothing_sending():
    b = T.build_block([], ["AIPG (401)"], "2026-07-20")
    assert "Nothing is sending" in b
    assert "Blind spots" in b


def test_insert_transform_inserts_above_first_section_and_updates_last_updated():
    block = T.build_block([("AvI", "c", 1, 1, 0, 0)], [], "2026-07-20")
    new = T._insert_transform(block, "2026-07-20")(STATE)
    assert new is not None
    assert "**Last updated:** 2026-07-20 (TP daily heartbeat)" in new
    # new block lands BEFORE the old 07-19 section
    assert new.index("TP daily -- 2026-07-20") < new.index("TP daily -- 2026-07-19")


def test_insert_transform_idempotent_when_today_present():
    # STATE already carries a 2026-07-19 block: same-day re-run must skip (None);
    # a new day inserts.
    assert T._insert_transform("x", "2026-07-19")(STATE) is None
    assert T._insert_transform("block\n", "2026-07-20")(STATE) is not None


def test_run_dry_run_returns_preview_no_commit():
    with mock.patch.object(T, "outbound_truth", return_value=([("AvI", "c", 1, 1, 0, 0)], [])):
        r = T.run_tp_daily(commit=False, token="tok")
    assert r["committed"] is False
    assert "preview" in r and "TP daily" in r["preview"]
