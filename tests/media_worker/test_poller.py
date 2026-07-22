import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.media_worker import poller


def _blobs(*pathnames):
    return [{"pathname": p, "url": "https://x/" + p, "size": 1} for p in pathnames]


def test_list_queue_filters_mp4_and_sorts(monkeypatch):
    monkeypatch.setattr(poller, "blob_list", lambda prefix, token: _blobs(
        "render_queue/aipg/b.mp4", "render_queue/aipg/a.mp4",
        "render_queue/aipg/notes.txt", "render_queue/aipg/thumb.png"))
    assert poller.list_queue("render_queue/", "tok") == [
        "render_queue/aipg/a.mp4", "render_queue/aipg/b.mp4"]


def test_rendered_stems_from_masters(monkeypatch):
    monkeypatch.setattr(poller, "blob_list", lambda prefix, token: _blobs(
        "renders_th/foo.mp4", "renders_th/foo.review.png",
        "renders_th/bar.mp4", "renders_th/REVIEW_LOG.md"))
    stems = poller.rendered_stems("renders_th/", "tok")
    assert stems == {"foo", "bar"}  # only .mp4 masters count; the .review.png sheet + .md are ignored


def test_brand_of_parses_subfolder_and_root():
    assert poller.brand_of("render_queue/aipg/foo.mp4", "render_queue/") == "aipg"
    assert poller.brand_of("render_queue/wd/clip.mp4", "render_queue/") == "wd"
    assert poller.brand_of("render_queue/loose.mp4", "render_queue/") is None


def test_pick_next_skips_rendered_and_respects_limit(monkeypatch):
    def fake_list(prefix, token):
        if prefix == "render_queue/":
            return _blobs("render_queue/aipg/a.mp4", "render_queue/aipg/b.mp4",
                          "render_queue/aipg/c.mp4")
        return _blobs("renders_th/a.mp4")  # 'a' already rendered
    monkeypatch.setattr(poller, "blob_list", fake_list)
    # 'a' skipped (rendered); limit 1 -> only 'b'
    assert poller.pick_next("render_queue/", "renders_th/", "tok", 1) == ["render_queue/aipg/b.mp4"]
    # limit 5 -> b, c (a still skipped)
    assert poller.pick_next("render_queue/", "renders_th/", "tok", 5) == [
        "render_queue/aipg/b.mp4", "render_queue/aipg/c.mp4"]


def test_poll_once_renders_eligible_with_brand(monkeypatch):
    calls = []

    def fake_list(prefix, token):
        if prefix == "render_queue/":
            return _blobs("render_queue/aipg/one.mp4", "render_queue/wd/two.mp4")
        return []  # nothing rendered yet

    def fake_run_job(env):
        calls.append({"take": env["TAKE_PATHNAME"], "brand": env.get("BRAND")})
        return {"master_url": "https://blob/renders_th/" + os.path.basename(env["TAKE_PATHNAME"])}

    monkeypatch.setattr(poller, "blob_list", fake_list)
    monkeypatch.setattr(poller, "run_job", fake_run_job)

    env = {"BLOB_READ_WRITE_TOKEN": "tok", "POLL_MAX_PER_CYCLE": "5"}
    result = poller.poll_once(env)

    assert [c["take"] for c in calls] == ["render_queue/aipg/one.mp4", "render_queue/wd/two.mp4"]
    assert calls[0]["brand"] == "aipg" and calls[1]["brand"] == "wd"
    assert result["queued"] == 2 and len(result["rendered"]) == 2


def test_poll_once_default_limit_is_one(monkeypatch):
    def fake_list(prefix, token):
        if prefix == "render_queue/":
            return _blobs("render_queue/aipg/a.mp4", "render_queue/aipg/b.mp4")
        return []
    rendered = []
    monkeypatch.setattr(poller, "blob_list", fake_list)
    monkeypatch.setattr(poller, "run_job", lambda env: rendered.append(env["TAKE_PATHNAME"]) or {"master_url": "u"})
    poller.poll_once({"BLOB_READ_WRITE_TOKEN": "tok"})  # no POLL_MAX_PER_CYCLE -> 1
    assert rendered == ["render_queue/aipg/a.mp4"]


def test_poll_once_guards_per_take(monkeypatch):
    def fake_list(prefix, token):
        if prefix == "render_queue/":
            return _blobs("render_queue/aipg/boom.mp4", "render_queue/aipg/ok.mp4")
        return []
    done = []

    def fake_run_job(env):
        if "boom" in env["TAKE_PATHNAME"]:
            raise RuntimeError("render blew up")
        done.append(env["TAKE_PATHNAME"])
        return {"master_url": "u"}

    monkeypatch.setattr(poller, "blob_list", fake_list)
    monkeypatch.setattr(poller, "run_job", fake_run_job)
    result = poller.poll_once({"BLOB_READ_WRITE_TOKEN": "tok", "POLL_MAX_PER_CYCLE": "5"})
    # boom failed but ok still rendered; poll_once did not raise
    assert done == ["render_queue/aipg/ok.mp4"]
    assert len(result["rendered"]) == 1
