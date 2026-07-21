"""Direct Vercel Blob HTTP I/O for the private store.

LIST and DOWNLOAD hit the Blob REST API directly with the read-write token as
a Bearer header (verified against the real private store: a GET without that
header returns only a few bytes of error body, not the file). PUT still
shells out to the `vercel` CLI; a bare HTTP PUT is not the verified path for
writing to a private store, and the CLI is the one that accepts
`--access private` + `--rw-token`. The CLI prints the resulting URL to
STDERR, so it is parsed out of stdout+stderr combined."""
from __future__ import annotations

import os
import re
import subprocess
from typing import List

import requests

DEFAULT_BASE = "https://blob.vercel-storage.com"
_URL_RE = re.compile(r"https://\S+")


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


def blob_put(local_path: str, pathname: str, token: str) -> str:
    """Upload `local_path` to the private store at the clean logical
    `pathname` via the vercel CLI (the only verified way to write to a
    private store). `--access private` is required (the store rejects a
    public upload) and `--add-random-suffix false` keeps the pathname clean.
    The CLI prints the resulting https URL to STDERR; parse it out of
    stdout+stderr combined and raise if none is found."""
    r = subprocess.run(
        ["vercel", "blob", "put", local_path,
         "--rw-token", token,
         "--access", "private",
         "--pathname", pathname,
         "--add-random-suffix", "false"],
        capture_output=True, text=True,
    )
    combined = (r.stdout or "") + "\n" + (r.stderr or "")
    m = _URL_RE.search(combined)
    if not m:
        raise RuntimeError(
            f"vercel blob put: no URL in output (exit {r.returncode}): {combined.strip()[:300]}")
    return m.group(0).rstrip(").,;\"'")
