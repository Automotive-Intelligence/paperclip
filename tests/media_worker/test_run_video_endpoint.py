import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient

import app as app_module
import services.media_worker as media_worker


def test_run_video_endpoint_returns_staged(monkeypatch):
    def fake_run_video(take, edit):
        assert take == "/some/take.mp4"
        assert edit == {"brand": "aipg"}
        return {"status": "staged", "master": "/x/m.mp4", "sheet": "/x/s.png", "flag": True}

    monkeypatch.setattr(media_worker, "run_video", fake_run_video)

    client = TestClient(app_module.app)
    resp = client.post(
        "/admin/run-video",
        json={"take": "/some/take.mp4", "edit": {"brand": "aipg"}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "staged"
    assert body["master"] == "/x/m.mp4"
