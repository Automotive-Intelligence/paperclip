import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker import blob_http


class _FakeResp:
    def __init__(self, json_data=None, content_chunks=None, status=200):
        self._json = json_data
        self._chunks = content_chunks or []
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1 << 20):
        for c in self._chunks:
            yield c


def test_blob_list_paginates_via_cursor(monkeypatch):
    calls = []
    page1 = {"blobs": [{"pathname": "a.bin", "url": "https://x/a-abc.bin", "size": 1}],
             "cursor": "cur1", "hasMore": True}
    page2 = {"blobs": [{"pathname": "b.bin", "url": "https://x/b-def.bin", "size": 2}],
             "cursor": None, "hasMore": False}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params)
        assert url == blob_http.DEFAULT_BASE
        assert headers == {"Authorization": "Bearer tok"}
        return _FakeResp(page1 if len(calls) == 1 else page2)

    monkeypatch.setattr(blob_http.requests, "get", fake_get)
    blobs = blob_http.blob_list("prefix/", "tok")
    assert [b["pathname"] for b in blobs] == ["a.bin", "b.bin"]
    assert calls[0].get("cursor") is None
    assert calls[1]["cursor"] == "cur1"


def test_blob_list_stops_when_no_more(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp({"blobs": [], "cursor": None, "hasMore": False})

    monkeypatch.setattr(blob_http.requests, "get", fake_get)
    assert blob_http.blob_list("empty/", "tok") == []


def test_blob_download_streams_with_auth_header(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, headers=None, stream=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["stream"] = stream
        return _FakeResp(content_chunks=[b"hello ", b"world"])

    monkeypatch.setattr(blob_http.requests, "get", fake_get)
    dest = str(tmp_path / "nested" / "take.mp4")
    blob_http.blob_download("https://x/take-abc.mp4", dest, "tok")
    assert seen["url"] == "https://x/take-abc.mp4"
    assert seen["headers"] == {"Authorization": "Bearer tok"}
    assert seen["stream"] is True
    assert open(dest, "rb").read() == b"hello world"


def test_blob_put_parses_url_from_stderr(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ("Uploading take.mp4...\n"
                   "https://abc123def.public.blob.vercel-storage.com/renders_th/take.mp4\n"
                   "Done.")

    def fake_run(argv, capture_output=None, text=None):
        captured["argv"] = argv
        return FakeCompleted()

    monkeypatch.setattr(blob_http.subprocess, "run", fake_run)
    url = blob_http.blob_put("/tmp/take.mp4", "renders_th/take.mp4", "tok")
    assert url == "https://abc123def.public.blob.vercel-storage.com/renders_th/take.mp4"

    argv = captured["argv"]
    assert argv[:4] == ["vercel", "blob", "put", "/tmp/take.mp4"]
    assert "--rw-token" in argv and "tok" in argv
    assert "--access" in argv and "private" in argv
    assert "--pathname" in argv and "renders_th/take.mp4" in argv
    assert "--add-random-suffix" in argv and "false" in argv


def test_blob_put_raises_when_no_url_found(monkeypatch):
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "Error: something failed"

    monkeypatch.setattr(blob_http.subprocess, "run", lambda *a, **k: FakeCompleted())
    with pytest.raises(RuntimeError):
        blob_http.blob_put("/tmp/x.mp4", "path/x.mp4", "tok")
