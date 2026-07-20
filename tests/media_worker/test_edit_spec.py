import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker.edit_spec import build_cut_argv


def test_minimal_edit_positional_args():
    argv = build_cut_argv({"brand": "aipg"}, "take.mp4", "w.json", "out.mp4")
    assert argv[:5] == ["python3", "scripts/cut_talking_head.py", "aipg", "take.mp4", "w.json"]
    assert argv[5] == "out.mp4"
    assert "--hook" not in argv


def test_full_edit_maps_every_flag():
    edit = {"brand": "aipg", "hook": "ON SCREEN HOOK",
            "cuts": ["61.2-68.4", "70-72.5"], "corrections": "tick=ticket",
            "broll_at": ["0|/s/a.mp4|1.0|4.0", "4.0|/s/b.mp4|0|3.5"]}
    argv = build_cut_argv(edit, "t.mp4", "w.json", "o.mp4")
    assert argv.count("--cut") == 2 and "61.2-68.4" in argv and "70-72.5" in argv
    assert argv.count("--broll-at") == 2 and "0|/s/a.mp4|1.0|4.0" in argv
    assert argv[argv.index("--hook") + 1] == "ON SCREEN HOOK"
    assert argv[argv.index("--corrections") + 1] == "tick=ticket"


def test_missing_brand_raises():
    with pytest.raises(ValueError):
        build_cut_argv({}, "t.mp4", "w.json", "o.mp4")
