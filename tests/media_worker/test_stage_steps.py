import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.media_worker import append_review_log, write_cmo_flag, stage_to_blob


def test_append_review_log_appends(tmp_path):
    p = tmp_path / "REVIEW_LOG.md"; p.write_text("head\n")
    append_review_log("2026-07-20 rendered clip6s", str(p))
    body = p.read_text()
    assert "head" in body and "2026-07-20 rendered clip6s" in body


def test_write_cmo_flag_appends_flag(tmp_path):
    p = tmp_path / "cmo_state.md"; p.write_text("# CMO\n")
    write_cmo_flag("VIDEO staged: clip6s master + sheet on Blob, awaiting file-133 gate", str(p))
    assert "VIDEO staged" in p.read_text()


def test_stage_to_blob_uploads_master_and_sheet():
    calls = []
    def fake_upload(files, root, manifest_path, runner=None):
        calls.extend(files); return {}
    res = stage_to_blob({"master": "/o/m.mp4", "sheet": "/o/s.png"}, fake_upload)
    assert "/o/m.mp4" in calls and "/o/s.png" in calls
