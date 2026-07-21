"""growth_monitor_engine: idempotent append-transform + dry-run preview."""
from unittest import mock

from services import growth_monitor_engine as Gm


def test_append_transform_appends_when_absent():
    out = Gm._append_transform("\n## 📈 Outbound monitor -- 2026-07-20\nbody\n", "2026-07-20")("PRIOR")
    assert out.startswith("PRIOR")
    assert "Outbound monitor -- 2026-07-20" in out


def test_append_transform_idempotent_when_today_present():
    existing = "stuff\n## 📈 Outbound monitor -- 2026-07-20\nx\n"
    assert Gm._append_transform("block", "2026-07-20")(existing) is None


def test_run_dry_run_preview_no_commit():
    with mock.patch.object(Gm, "build_block", return_value="\n## 📈 Outbound monitor -- 2026-07-20\nok\n"):
        r = Gm.run_growth_monitor(commit=False, token="tok")
    assert r["committed"] is False and "preview" in r
