"""services/slipstream_github.py -- publish a blog post to a brand repo via the
GitHub REST Contents API (branch + files + PR). No local git clone, no npm.

Vercel auto-builds the PR's preview = the build-gate. Auto-merge (a later
increment) waits on that preview build succeeding. Uses SLIPSTREAM_GH_TOKEN
(fine-grained PAT: Contents + Pull requests write).
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Callable, Dict, Union

import requests

logger = logging.getLogger(__name__)

_API = "https://api.github.com/repos"


class PublishError(Exception):
    pass


def _default_http(method: str, url: str, token: str, json_body: Any = None) -> Dict[str, Any]:
    r = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        json=json_body, timeout=45,
    )
    if not r.ok:
        raise PublishError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def _encode(content: Union[str, bytes]) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return base64.b64encode(raw).decode("ascii")


def publish_post(
    repo: str,
    branch: str,
    files: Dict[str, Union[str, bytes]],
    pr_title: str,
    pr_body: str,
    token: str,
    *,
    base: str = "main",
    http: Callable = _default_http,
) -> str:
    """Create `branch` off `base`, commit each file, open a PR. Returns the PR URL."""
    if not token:
        raise PublishError("SLIPSTREAM_GH_TOKEN missing")
    repo_api = f"{_API}/{repo}"

    base_sha = http("GET", f"{repo_api}/git/ref/heads/{base}", token)["object"]["sha"]
    http("POST", f"{repo_api}/git/refs", token,
         {"ref": f"refs/heads/{branch}", "sha": base_sha})

    for path, content in files.items():
        http("PUT", f"{repo_api}/contents/{path}", token, {
            "message": f"{pr_title} :: {path}",
            "content": _encode(content),
            "branch": branch,
        })

    pr = http("POST", f"{repo_api}/pulls", token,
              {"title": pr_title, "head": branch, "base": base, "body": pr_body})
    return pr["html_url"]


def merge_when_green(
    repo: str,
    pr_url: str,
    token: str,
    *,
    timeout_polls: int = 40,
    poll_sleep: float = 15.0,
    http: Callable = _default_http,
    sleep: Callable = time.sleep,
) -> Dict[str, Any]:
    """Poll the PR's Vercel build check; squash-merge on success, HOLD on failure
    or timeout. This is auto-publish gated on the build-gate, so a broken post is
    never merged to main."""
    pr_num = int(pr_url.rstrip("/").split("/")[-1])
    repo_api = f"{_API}/{repo}"
    sha = http("GET", f"{repo_api}/pulls/{pr_num}", token)["head"]["sha"]
    for _ in range(timeout_polls):
        runs = http("GET", f"{repo_api}/commits/{sha}/check-runs", token).get("check_runs", [])
        vercel = [r for r in runs if r.get("name") == "Vercel"]
        if vercel:
            concl = vercel[0].get("conclusion")
            if concl == "success":
                http("PUT", f"{repo_api}/pulls/{pr_num}/merge", token, {"merge_method": "squash"})
                return {"merged": True, "pr_url": pr_url}
            if concl in ("failure", "cancelled", "timed_out", "action_required", "startup_failure"):
                return {"merged": False, "reason": f"vercel build {concl}", "pr_url": pr_url}
        sleep(poll_sleep)
    return {"merged": False, "reason": "vercel build timeout (still pending)", "pr_url": pr_url}
