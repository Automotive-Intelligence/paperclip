"""Direct Vercel Blob HTTP I/O for the private store.

All three operations hit the Blob REST API directly with the read-write token
as a Bearer header -- no vercel CLI / Node in the container. LIST and DOWNLOAD
require the Bearer header (verified against the real private store: a GET
without it returns only a few bytes of error body, not the file). PUT writes
to the private store with the `x-vercel-blob-access: private` header (the
header @vercel/blob itself sends) and returns the resulting URL from the JSON
response body, so nothing needs to be parsed out of CLI output."""
from __future__ import annotations

import os
from typing import List

import requests

DEFAULT_BASE = "https://blob.vercel-storage.com"


def blob_list(prefix: str, token: str, base: str = DEFAULT_BASE) -> List[dict]:
    """List blobs whose pathname starts with `prefix`, paginating via the
    cursor until the response says hasMore is false. Returns the combined
    `blobs` list (each with the clean `pathname` and the random-suffixed
    `url` needed for download)."""
    blobs: List[dict] = []
    cursor = None
    while True:
        params = {"prefix": prefix, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(base, params=params,
                            headers={"Authorization": f"Bearer {token}"}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        blobs.extend(data.get("blobs", []))
        if not data.get("hasMore"):
            break
        cursor = data.get("cursor")
        if not cursor:
            break
    return blobs


def blob_download(url: str, dest: str, token: str) -> None:
    """Stream-download a blob's `url` (the random-suffixed one from
    blob_list, not the clean pathname) to `dest`, making parent dirs as
    needed. The Authorization header is mandatory: a GET without it returns
    a short error body instead of the file."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                        stream=True, timeout=600)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)


_CONTENT_TYPES = {".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg", ".json": "application/json", ".wav": "audio/wav"}


def blob_put(local_path: str, pathname: str, token: str, base: str = DEFAULT_BASE) -> str:
    """Upload `local_path` to the private store at the clean logical `pathname`
    via a direct HTTP PUT. Private-store access is set with the
    `x-vercel-blob-access: private` header (the header @vercel/blob itself
    sends); a PUT without it is rejected as "public access on a private store".
    Raw HTTP (no vercel CLI), so this works in the container -- the CLI hangs
    on an interactive account login when none is present.

    `x-add-random-suffix: 0` keeps the pathname clean (no random URL suffix),
    and `x-allow-overwrite: 1` lets a repeated PUT to an existing pathname
    replace it instead of being rejected as "blob already exists". Both the
    REVIEW_LOG.md (re-put every run) and a re-render of the same take write
    the same pathname twice, so overwrite must be allowed or the second write
    fails."""
    from urllib.parse import quote
    with open(local_path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(pathname)[1].lower()
    resp = requests.put(
        f"{base}/{quote(pathname, safe='/')}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "x-vercel-blob-access": "private",
            "x-content-type": _CONTENT_TYPES.get(ext, "application/octet-stream"),
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        },
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()["url"]
