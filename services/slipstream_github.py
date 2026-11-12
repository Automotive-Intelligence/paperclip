"""services/slipstream_github.py -- publish a blog post to a brand repo via the
GitHub REST Contents API (branch + files + PR). No local git clone, no npm.

Vercel auto-builds the PR's preview = the build-gate. Auto-merge (a later
increment) waits on that preview build succeeding. Uses SLIPSTREAM_GH_TOKEN
(fine-grained PAT: Contents + Pull requests write).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

_API = "https://api.github.com/repos"
_VERCEL_API = "https://api.vercel.com"


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


def _existing_file_sha(http: Callable, repo_api: str, path: str, branch: str, token: str) -> Union[str, None]:
    """Return the current blob sha of `path` on `branch`, or None if it does not
    exist yet. The GitHub Contents API requires the current sha to UPDATE an
    existing file (create-new files must omit it); WD's ts_posts_array adapter
    rewrites one existing file (src/content/posts.ts), so its PUT needs the sha,
    while the MDX brands create fresh {slug}.mdx files (404 here -> no sha)."""
    try:
        resp = http("GET", f"{repo_api}/contents/{path}?ref={branch}", token)
    except PublishError as e:
        if "-> 404" in str(e) or " 404:" in str(e):
            return None
        raise
    return resp.get("sha") if isinstance(resp, dict) else None


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
    try:
        http("POST", f"{repo_api}/git/refs", token,
             {"ref": f"refs/heads/{branch}", "sha": base_sha})
    except PublishError as e:
        # A leftover branch of the SAME name must never wedge the engine. The
        # 2026-07-31 BAE miss was exactly this: a stale slipstream/<slug>-<date>
        # branch (from a hand-closed duplicate PR) made every re-run's branch
        # create 422 "Reference already exists", so run_brand raised, opened NO
        # PR, and the brand produced NOTHING on no rail. On a 422 we reset the
        # existing ref to a fresh base and reuse it -- idempotent, since we are
        # about to (re)commit this run's files onto it anyway.
        if "-> 422" not in str(e) and " 422:" not in str(e):
            raise
        logger.warning("[slipstream] branch %s already exists; resetting to %s and reusing", branch, base)
        http("PATCH", f"{repo_api}/git/refs/heads/{branch}", token,
             {"sha": base_sha, "force": True})

    for path, content in files.items():
        body: Dict[str, Any] = {
            "message": f"{pr_title} :: {path}",
            "content": _encode(content),
            "branch": branch,
        }
        # Updating an existing file requires its current sha; creating a new one
        # must omit it. The branch was just cut from `base`, so the file's sha on
        # `branch` is the one to send.
        existing_sha = _existing_file_sha(http, repo_api, path, branch, token)
        if existing_sha:
            body["sha"] = existing_sha
        http("PUT", f"{repo_api}/contents/{path}", token, body)

    pr = http("POST", f"{repo_api}/pulls", token,
              {"title": pr_title, "head": branch, "base": base, "body": pr_body})
    return pr["html_url"]


def _default_vercel_http(method: str, url: str, token: str, json_body: Any = None) -> Dict[str, Any]:
    """HTTP for the Vercel REST API (Bearer VERCEL_API_TOKEN). Separate from the
    GitHub _default_http because it uses a different host, token, and headers."""
    r = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}"},
        json=json_body, timeout=30,
    )
    if not r.ok:
        raise PublishError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def _vercel_deployment_for(
    vercel_http: Callable,
    vercel_token: str,
    *,
    sha: str,
    ref: Optional[str] = None,
    project_id: Optional[str] = None,
    team_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the NEWEST Vercel deployment for the PR's head commit `sha` (falling
    back to the branch `ref` if the commit is not yet mapped), or None if Vercel
    has not created a deployment yet. Vercel returns deployments newest-first, so
    index 0 is the latest build attempt for that commit/branch."""
    def _query(**meta: str) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {"limit": "20"}
        if project_id:
            params["projectId"] = project_id
        if team_id:
            params["teamId"] = team_id
        params.update(meta)
        url = f"{_VERCEL_API}/v6/deployments?{urlencode(params)}"
        return vercel_http("GET", url, vercel_token).get("deployments") or []

    # Primary: match the exact head commit sha (unique per push, most precise).
    deployments = _query(**{"meta-githubCommitSha": sha})
    # Fallback: the sha->deployment mapping can lag; match the branch ref instead.
    if not deployments and ref:
        deployments = _query(**{"meta-githubCommitRef": ref})
    return deployments[0] if deployments else None


_VERCEL_BOT_LOGIN = "vercel[bot]"
_VC_PAYLOAD = re.compile(r"\[vc\]:\s*#[^:\s]+:([A-Za-z0-9+/=]+)")


def _vercel_bot_comment_state(
    http: Callable, repo_api: str, pr_num: int, token: str, project_id: Optional[str],
) -> Optional[str]:
    """Second source for the build gate: the Vercel GitHub integration's own PR
    comment. Returns "READY", "ERROR", or None (unknown / not yet / stale).

    Why this exists: VERCEL_API_TOKEN died in the 2026-10-21 breach rotation and
    every auto-merge since held for 10 minutes and then gave up (AIPG PR #57 sat
    open from 10-21; PR #61 on 11-11 did the same), so no blog published even
    after SLIPSTREAM_GH_TOKEN was replaced. The fine-grained PAT cannot read
    commit statuses, check-runs or deployments (all 403), but it CAN read PR
    comments, and vercel[bot] posts one per PR carrying a machine-readable
    payload with each project's deploy status.

    Fail-closed rules, all required:
      - a per-brand vercel_project_id is configured and a project entry in the
        payload matches it exactly;
      - the comment author is the Vercel bot account;
      - the comment was last updated AFTER the PR's newest commit, so a status
        left over from an earlier commit can never approve a later one.
    Anything else returns None and the caller keeps polling, then HOLDS.
    """
    if not project_id:
        return None
    try:
        commits = http("GET", f"{repo_api}/pulls/{pr_num}/commits?per_page=100", token)
        if not isinstance(commits, list) or not commits:
            return None
        head_at = max(
            ((c.get("commit") or {}).get("committer") or {}).get("date") or "" for c in commits
        )
        comments = http("GET", f"{repo_api}/issues/{pr_num}/comments?per_page=100", token)
        if not head_at or not isinstance(comments, list):
            return None
    except Exception:
        return None
    for c in reversed(comments):
        user = c.get("user") or {}
        if user.get("login") != _VERCEL_BOT_LOGIN or user.get("type") != "Bot":
            continue
        # ISO-8601 UTC "Z" timestamps of equal precision compare correctly as strings.
        if (c.get("updated_at") or "") <= head_at:
            return None
        m = _VC_PAYLOAD.search(c.get("body") or "")
        if not m:
            return None
        try:
            payload = json.loads(base64.b64decode(m.group(1)))
        except Exception:
            return None
        for proj in payload.get("projects") or []:
            if proj.get("projectId") != project_id:
                continue
            status = (proj.get("nextCommitStatus") or "").upper()
            if status == "DEPLOYED":
                return "READY"
            if status in ("FAILED", "ERROR", "CANCELED", "CANCELLED"):
                return "ERROR"
            return None
        return None
    return None


def merge_when_green(
    repo: str,
    pr_url: str,
    token: str,
    *,
    timeout_polls: int = 40,
    poll_sleep: float = 15.0,
    http: Callable = _default_http,
    sleep: Callable = time.sleep,
    vercel_project_id: Optional[str] = None,
    vercel_team_id: Optional[str] = None,
    vercel_token: Optional[str] = None,
    vercel_http: Callable = _default_vercel_http,
) -> Dict[str, Any]:
    """Poll the PR's Vercel preview build via the VERCEL API; squash-merge on
    READY, HOLD on ERROR/CANCELED or timeout. This is auto-publish gated on the
    build-gate, so a broken post is never merged to main.

    Build verification uses the Vercel REST API (VERCEL_API_TOKEN) rather than
    GitHub check-runs: the SLIPSTREAM_GH_TOKEN fine-grained PAT lacks checks:read,
    so GET /commits/{sha}/check-runs 403'd on every poll and no PR ever merged
    (the 2-week publish blackout). The GitHub token is still used for the two
    GitHub calls -- reading the PR head sha and the squash-merge PUT.
    """
    pr_num = int(pr_url.rstrip("/").split("/")[-1])
    repo_api = f"{_API}/{repo}"
    head = http("GET", f"{repo_api}/pulls/{pr_num}", token)["head"]
    sha = head["sha"]
    ref = head.get("ref")

    vercel_token = (vercel_token or os.getenv("VERCEL_API_TOKEN") or "").strip()
    if not vercel_token and not vercel_project_id:
        # Fail CLOSED: no API token and no project id to match the Vercel bot
        # comment against means there is no way to verify the build.
        return {"merged": False, "reason": "VERCEL_API_TOKEN missing (cannot verify build)",
                "pr_url": pr_url}

    last_err: Optional[str] = None
    comment_ready_streak = 0
    for _ in range(timeout_polls):
        dep = None
        api_unavailable = not vercel_token
        if vercel_token:
            try:
                dep = _vercel_deployment_for(
                    vercel_http, vercel_token,
                    sha=sha, ref=ref,
                    project_id=vercel_project_id, team_id=vercel_team_id,
                )
            except Exception as e:
                # A transient Vercel API blip must not crash the run (which already
                # opened the PR). Remember it and keep polling; a persistent error
                # falls through to the bot-comment gate, then a timeout HOLD.
                last_err = str(e)[:200]
                api_unavailable = True
        if dep is not None:
            state = (dep.get("readyState") or dep.get("state") or "").upper()
            if state == "READY":
                http("PUT", f"{repo_api}/pulls/{pr_num}/merge", token, {"merge_method": "squash"})
                return {"merged": True, "pr_url": pr_url}
            if state in ("ERROR", "CANCELED"):
                return {"merged": False, "reason": f"vercel build {state.lower()}", "pr_url": pr_url}
            # BUILDING / QUEUED / INITIALIZING -> keep polling.
        elif api_unavailable:
            # The Vercel API cannot answer (token missing, revoked or erroring).
            # Fall back to the Vercel bot's PR comment. READY must be seen on two
            # consecutive polls before merging, so a comment mid-update never wins.
            cstate = _vercel_bot_comment_state(http, repo_api, pr_num, token, vercel_project_id)
            if cstate == "ERROR":
                return {"merged": False, "reason": "vercel build error (per Vercel bot comment)",
                        "pr_url": pr_url}
            comment_ready_streak = comment_ready_streak + 1 if cstate == "READY" else 0
            if comment_ready_streak >= 2:
                http("PUT", f"{repo_api}/pulls/{pr_num}/merge", token, {"merge_method": "squash"})
                logger.warning("[slipstream] merged %s on the Vercel bot comment gate "
                               "(Vercel API unavailable: %s)", pr_url, last_err or "no token")
                return {"merged": True, "pr_url": pr_url, "gate": "vercel_bot_comment"}
        sleep(poll_sleep)
    reason = "vercel build timeout (still pending)"
    if last_err:
        reason = f"vercel verify never returned a ready build (last error: {last_err})"
    return {"merged": False, "reason": reason, "pr_url": pr_url}
