import os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker.render import render_one

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("whisper-cli")),
    reason="ffmpeg + whisper-cli required (present in the worker image / local Mac)")


def test_render_one_produces_master_and_sheet(tmp_path):
    model = os.environ.get("WHISPER_MODEL",
                           os.path.expanduser("~/stock_library/.whisper_models/ggml-small.en.bin"))
    take = os.environ.get("SMOKE_TAKE")  # a real short AIPG take; set in CI/local
    if not (os.path.exists(model) and take and os.path.exists(take)):
        pytest.skip("model + SMOKE_TAKE required for the render smoke")
    res = render_one({"brand": "aipg"}, take, model, str(tmp_path),
                     cut_script=os.path.expanduser("~/avo-telemetry/scripts/cut_talking_head.py"),
                     sheet_script=os.path.expanduser("~/avo-telemetry/scripts/video_review_sheet.py"))
    assert os.path.getsize(res["master"]) > 0
    assert os.path.getsize(res["sheet"]) > 0
