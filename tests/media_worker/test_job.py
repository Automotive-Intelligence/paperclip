import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.media_worker import job


def test_run_job_pulls_model_and_take_renders_and_pushes_master(monkeypatch, tmp_path, capsys):
    work = str(tmp_path / "work")
    calls = {"list": [], "download": [], "render": [], "put": []}

    def fake_list(prefix, token):
        calls["list"].append(prefix)
        assert token == "tok"
        if "whisper_models" in prefix:
            return [{"pathname": prefix + "ggml-small.en.bin",
                     "url": "https://x/model-abc.bin", "size": 100}]
        # fetch_take lists the PARENT prefix; return the full take pathname under it
        return [{"pathname": "raw_shoot_2026-07/aipg/riverside.mp4",
                 "url": "https://x/riverside-abc.mp4", "size": 10}]

    def fake_download(url, dest, token):
        calls["download"].append((url, dest))
        assert token == "tok"

    def fake_render(edit, take, model, out_dir, cut_script, sheet_script):
        calls["render"].append((edit, take, model, out_dir, cut_script, sheet_script))
        return {"master": os.path.join(out_dir, "riverside.mp4"),
                "sheet": os.path.join(out_dir, "riverside.review.png"),
                "words": os.path.join(out_dir, "riverside.json")}

    def fake_put(local_path, pathname, token):
        calls["put"].append((local_path, pathname))
        assert token == "tok"
        return f"https://blob.example/{pathname}"

    monkeypatch.setattr(job, "blob_list", fake_list)
    monkeypatch.setattr(job, "blob_download", fake_download)
    monkeypatch.setattr(job, "render_one", fake_render)
    monkeypatch.setattr(job, "blob_put", fake_put)
    monkeypatch.setattr(job, "append_blob_review_log", lambda entry, token, prefix="renders_th/": None)

    env = {
        "BLOB_READ_WRITE_TOKEN": "tok",
        "TAKE_PATHNAME": "raw_shoot_2026-07/aipg/riverside.mp4",
        "WORK": work,
    }
    result = job.run_job(env)

    # model pulled once, take pulled once, one render, two pushes (master + sheet)
    assert len(calls["list"]) == 2
    assert len(calls["download"]) == 2
    assert len(calls["render"]) == 1
    assert len(calls["put"]) == 2

    edit, take, model, out_dir, cut_script, sheet_script = calls["render"][0]
    assert edit == {"brand": "aipg"}  # BRAND not set -> default "aipg"
    assert take == os.path.join(work, "take.mp4")
    assert model == os.path.join(work, "model", "ggml-small.en.bin")
    assert cut_script == job.CUT_SCRIPT
    assert sheet_script == job.SHEET_SCRIPT

    put_pathnames = [p for (_, p) in calls["put"]]
    assert put_pathnames == ["renders_th/riverside.mp4", "renders_th/riverside.review.png"]

    assert result["master_url"] == "https://blob.example/renders_th/riverside.mp4"
    assert result["sheet_url"] == "https://blob.example/renders_th/riverside.review.png"

    out = capsys.readouterr().out
    assert "MASTER_URL=https://blob.example/renders_th/riverside.mp4" in out
    assert "SHEET_URL=https://blob.example/renders_th/riverside.review.png" in out


def test_run_job_honors_edit_json_override_and_out_prefix(monkeypatch, tmp_path):
    seen = {}

    def fake_list(prefix, token):
        if "whisper_models" in prefix:
            return [{"pathname": prefix + "ggml-small.en.bin", "url": "https://x/m-abc.bin", "size": 1}]
        return [{"pathname": "clip.mp4", "url": "https://x/clip-abc.mp4", "size": 1}]

    def fake_download(url, dest, token):
        pass

    def fake_render(edit, take, model, out_dir, cut_script, sheet_script):
        seen["edit"] = edit
        return {"master": "m.mp4", "sheet": "s.png"}

    def fake_put(local_path, pathname, token):
        seen.setdefault("pushed", []).append(pathname)
        return "https://blob.example/" + pathname

    monkeypatch.setattr(job, "blob_list", fake_list)
    monkeypatch.setattr(job, "blob_download", fake_download)
    monkeypatch.setattr(job, "render_one", fake_render)
    monkeypatch.setattr(job, "blob_put", fake_put)
    monkeypatch.setattr(job, "append_blob_review_log", lambda entry, token, prefix="renders_th/": None)

    env = {
        "BLOB_READ_WRITE_TOKEN": "tok",
        "TAKE_PATHNAME": "clip.mp4",
        "WORK": str(tmp_path),
        "BRAND": "wd",
        "EDIT_JSON": '{"brand": "wd", "hook": "opener"}',
        "OUT_PREFIX": "custom_out/",
    }
    job.run_job(env)

    assert seen["edit"] == {"brand": "wd", "hook": "opener"}
    assert seen["pushed"] == ["custom_out/clip.mp4", "custom_out/clip.review.png"]


def test_append_blob_review_log_creates_when_absent(monkeypatch):
    puts = []

    def fake_list(prefix, token):
        assert prefix == "renders_th/"
        assert token == "tok"
        return []  # no REVIEW_LOG.md on Blob yet

    def fake_download(url, dest, token):
        raise AssertionError("should not download when REVIEW_LOG.md is absent")

    def fake_put(local_path, pathname, token):
        assert pathname == "renders_th/REVIEW_LOG.md"
        assert token == "tok"
        with open(local_path, "r", encoding="utf-8") as f:
            puts.append(f.read())
        return "https://blob.example/renders_th/REVIEW_LOG.md"

    monkeypatch.setattr(job, "blob_list", fake_list)
    monkeypatch.setattr(job, "blob_download", fake_download)
    monkeypatch.setattr(job, "blob_put", fake_put)

    entry = ("STAGED brand=aipg take=raw_shoot_2026-07/aipg/riverside.mp4 "
              "master=https://blob.example/renders_th/riverside.mp4 "
              "sheet=https://blob.example/renders_th/riverside.review.png "
              ":: awaiting file-133/117 + CMO gate, NOT scheduled")
    job.append_blob_review_log(entry, "tok")

    assert len(puts) == 1
    assert "https://blob.example/renders_th/riverside.mp4" in puts[0]
    assert "NOT scheduled" in puts[0]


def test_append_blob_review_log_appends_to_existing(monkeypatch, tmp_path):
    existing_path = tmp_path / "existing_review_log.md"
    existing_path.write_text("- 2026-07-01T00:00:00+00:00 STAGED old entry\n", encoding="utf-8")
    puts = []

    def fake_list(prefix, token):
        return [{"pathname": "renders_th/REVIEW_LOG.md", "url": "https://x/log-abc.md", "size": 10}]

    def fake_download(url, dest, token):
        assert url == "https://x/log-abc.md"
        with open(dest, "w", encoding="utf-8") as f:
            f.write(existing_path.read_text(encoding="utf-8"))

    def fake_put(local_path, pathname, token):
        with open(local_path, "r", encoding="utf-8") as f:
            puts.append(f.read())
        return "https://blob.example/" + pathname

    monkeypatch.setattr(job, "blob_list", fake_list)
    monkeypatch.setattr(job, "blob_download", fake_download)
    monkeypatch.setattr(job, "blob_put", fake_put)

    job.append_blob_review_log("STAGED brand=wd take=x.mp4 master=m sheet=s :: NOT scheduled", "tok")

    assert len(puts) == 1
    assert "STAGED old entry" in puts[0]  # existing content preserved
    assert "STAGED brand=wd take=x.mp4 master=m sheet=s :: NOT scheduled" in puts[0]


def test_run_job_survives_review_log_failure(monkeypatch, tmp_path, capsys):
    """run_job must not raise even if the REVIEW_LOG write fails; the master
    is already safely on Blob at that point."""
    def fake_list(prefix, token):
        if "whisper_models" in prefix:
            return [{"pathname": prefix + "ggml-small.en.bin", "url": "https://x/m-abc.bin", "size": 1}]
        return [{"pathname": "clip.mp4", "url": "https://x/clip-abc.mp4", "size": 1}]

    def fake_download(url, dest, token):
        pass

    def fake_render(edit, take, model, out_dir, cut_script, sheet_script):
        return {"master": "m.mp4", "sheet": "s.png"}

    def fake_put(local_path, pathname, token):
        return "https://blob.example/" + pathname

    def fake_append_blob_review_log(entry, token, prefix="renders_th/"):
        raise RuntimeError("blob down")

    monkeypatch.setattr(job, "blob_list", fake_list)
    monkeypatch.setattr(job, "blob_download", fake_download)
    monkeypatch.setattr(job, "render_one", fake_render)
    monkeypatch.setattr(job, "blob_put", fake_put)
    monkeypatch.setattr(job, "append_blob_review_log", fake_append_blob_review_log)

    env = {
        "BLOB_READ_WRITE_TOKEN": "tok",
        "TAKE_PATHNAME": "clip.mp4",
        "WORK": str(tmp_path),
    }
    result = job.run_job(env)  # must not raise

    assert result["master_url"] == "https://blob.example/renders_th/clip.mp4"
    assert "REVIEW_LOG update FAILED" in capsys.readouterr().out
