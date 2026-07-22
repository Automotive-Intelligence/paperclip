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


class _FakePut:
    def __init__(self, captured):
        self._c = captured

    def __call__(self, url, data=None, headers=None, timeout=None):
        self._c.update(url=url, data=data, headers=headers)
        c = self._c

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"url": "https://x.private.blob.vercel-storage.com/" + c["path"]}
        return Resp()


def test_blob_put_sets_private_access_header(monkeypatch, tmp_path):
    f = tmp_path / "take.mp4"
    f.write_bytes(b"videobytes")
    captured = {"path": "renders_th/take.mp4"}
    monkeypatch.setattr(blob_http.requests, "put", _FakePut(captured))
    url = blob_http.blob_put(str(f), "renders_th/take.mp4", "tok")
    assert url == "https://x.private.blob.vercel-storage.com/renders_th/take.mp4"
    h = captured["headers"]
    assert h["x-vercel-blob-access"] == "private"      # the header that makes a private PUT work
    assert h["Authorization"] == "Bearer tok"
    assert h["x-content-type"] == "video/mp4"
    assert captured["data"] == b"videobytes"
    assert captured["url"].endswith("renders_th/take.mp4")


def test_blob_put_url_encodes_spaces(monkeypatch, tmp_path):
    f = tmp_path / "t.mp4"
    f.write_bytes(b"d")
    captured = {"path": "renders_th/a.mp4"}
    monkeypatch.setattr(blob_http.requests, "put", _FakePut(captured))
    blob_http.blob_put(str(f), "renders_th/riverside they call.mp4", "tok")
    assert "%20" in captured["url"] and "/renders_th/" in captured["url"]  # spaces encoded, slash kept


def test_blob_put_raises_on_http_error(monkeypatch, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"d")

    class Resp:
        def raise_for_status(self):
            raise RuntimeError("bad request")

    monkeypatch.setattr(blob_http.requests, "put", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError):
        blob_http.blob_put(str(f), "path/x.mp4", "tok")
