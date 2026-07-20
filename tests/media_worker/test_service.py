import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import services.media_worker as media_worker


def test_run_video_returns_staged_status_with_render_result(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_MODEL", str(tmp_path / "fake-model.bin"))
    calls = []

    def fake_render_one(edit, take, model, out_dir, cut_script, sheet_script):
        calls.append({
            "edit": edit,
            "take": take,
            "model": model,
            "out_dir": out_dir,
            "cut_script": cut_script,
            "sheet_script": sheet_script,
        })
        return {"master": "/x/m.mp4", "sheet": "/x/s.png", "words": "/x/w.json"}

    monkeypatch.setattr(media_worker, "render_one", fake_render_one)

    take = "/some/take.mp4"
    edit = {"brand": "aipg"}
    result = media_worker.run_video(take, edit)

    # orchestration contract: staged status plus the render's own outputs, verbatim
    assert result["status"] == "staged"
    assert result["master"] == "/x/m.mp4"
    assert result["sheet"] == "/x/s.png"
    assert result["words"] == "/x/w.json"

    # render_one was called exactly once, with the edit and take we passed in
    assert len(calls) == 1
    assert calls[0]["edit"] == edit
    assert calls[0]["take"] == take


def test_run_video_never_calls_render_one_more_than_once(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_MODEL", str(tmp_path / "fake-model.bin"))
    calls = []

    def fake_render_one(edit, take, model, out_dir, cut_script, sheet_script):
        calls.append(1)
        return {"master": "/x/m.mp4", "sheet": "/x/s.png", "words": "/x/w.json"}

    monkeypatch.setattr(media_worker, "render_one", fake_render_one)
    media_worker.run_video("/another/take.mp4", {"brand": "wd"})
    assert len(calls) == 1
