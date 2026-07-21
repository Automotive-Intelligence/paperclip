"""services/avo_state_commit.py -- robust read-modify-write of an avo-telemetry
state file from the cloud (no local checkout).

The laptop monitors edited a local file and `git push -q || true` -- which SILENTLY
dropped the write on any non-fast-forward (team_principal_state.md is high-churn:
many processes commit to it). The cloud port MUST land in the repo, so this does a
GitHub Contents-API read-modify-write and RETRIES the whole read+transform+commit on
a 409 conflict (re-reading the fresh sha each attempt, so it never clobbers a
concurrent write). The transform returning None means "already applied" (idempotent
skip), which is how the daily jobs avoid a double-write.

Every network call is a module seam so tests never hit the wire.
"""
from __future__ import annotations

import base64
import logging
from typing import Callable, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_REPO = "salesdroid/avo-telemetry"
_API = "https://api.github.com/repos"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _read(path: str, token: str) -> Tuple[str, Optional[str]]:
    """Return (content, sha). sha is None if the file does not exist yet."""
    r = requests.get(f"{_API}/{_REPO}/contents/{path}", headers=_headers(token), timeout=30)
    if r.status_code == 404:
        return "", None
    if not r.ok:
        raise RuntimeError(f"read {path} failed: {r.status_code} {r.text[:120]}")
    data = r.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data.get("sha")


def _put(path: str, content: str, sha: Optional[str], message: str, token: str) -> Tuple[bool, int]:
    body = {"message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": "main"}
    if sha:
        body["sha"] = sha
    r = requests.put(f"{_API}/{_REPO}/contents/{path}", headers=_headers(token),
                     json=body, timeout=45)
    return r.ok, r.status_code


def update_state(path: str, transform: Callable[[str], Optional[str]], message: str,
                 token: str, *, retries: int = 4,
                 read: Callable = _read, put: Callable = _put) -> dict:
    """Read `path`, apply `transform(content) -> new_content | None`, commit. On a
    409 (someone else committed between our read and write) re-read the fresh sha
    and re-apply the transform, up to `retries` times. transform returning None =
    nothing to do (idempotent skip). Returns {committed, skipped?}."""
    if not token:
        return {"committed": False, "error": "no token"}
    last = 0
    for _ in range(retries):
        content, sha = read(path, token)
        new = transform(content)
        if new is None:
            return {"committed": False, "skipped": True}
        ok, status = put(path, new, sha, message, token)
        if ok:
            return {"committed": True}
        last = status
        if status != 409:
            raise RuntimeError(f"commit {path} failed: {status}")
        logger.warning("[avo-state] %s 409 conflict, re-reading + retrying", path)
    raise RuntimeError(f"commit {path} failed after {retries} conflict retries (last {last})")
