"""merge_when_green verifies the PR's preview build via the VERCEL API (not
GitHub check-runs: the fine-grained PAT lacks checks:read and 403'd every merge,
which caused the 2-week publish blackout). These tests drive the Vercel
deployment readyState and assert the merge/HOLD semantics are preserved."""
from services import slipstream_github as sg


class _GH:
    """GitHub http: serves the PR head sha/ref and records the squash-merge."""

    def __init__(self):
        self.merged = False

    def __call__(self, method, url, token, json_body=None):
        if url.endswith("/pulls/30"):
            return {"head": {"sha": "abc123", "ref": "slipstream/foo-2026-07-30"}}
        if url.endswith("/pulls/30/merge"):
            assert json_body == {"merge_method": "squash"}
            self.merged = True
            return {"merged": True}
        raise AssertionError(f"unexpected GitHub {method} {url}")


class _Vercel:
    """Vercel http: returns a scripted sequence of readyState values, one per
    poll. A None entry means 'no deployment yet' (empty list -> keep polling).
    Only the primary meta-githubCommitSha query advances the script; the branch
    fallback returns empty so tests stay one-pop-per-poll."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def __call__(self, method, url, token, json_body=None):
        self.calls.append(url)
        if "meta-githubCommitSha" in url:
            state = self.states.pop(0) if self.states else "READY"
            if state is None:
                return {"deployments": []}
            return {"deployments": [{"readyState": state, "meta": {"githubCommitSha": "abc123"}}]}
        return {"deployments": []}  # branch-ref fallback


def _run(vercel, gh=None, **kw):
    gh = gh or _GH()
    out = sg.merge_when_green(
        "salesdroid/automotive-intelligence",
        "https://github.com/salesdroid/automotive-intelligence/pull/30",
        "ghtok",
        http=gh, vercel_http=vercel, vercel_token="vtok",
        vercel_project_id="prj_x", vercel_team_id="team_x",
        poll_sleep=0, **kw,
    )
    return out, gh


def test_merges_when_vercel_ready():
    v = _Vercel([None, "BUILDING", "READY"])  # not-created, building, then ready
    out, gh = _run(v)
    assert out["merged"] is True
    assert gh.merged is True


def test_holds_when_vercel_error():
    out, gh = _run(_Vercel(["ERROR"]))
    assert out["merged"] is False
    assert "error" in out["reason"].lower()
    assert gh.merged is False


def test_holds_when_vercel_canceled():
    out, gh = _run(_Vercel(["CANCELED"]))
    assert out["merged"] is False
    assert "cancel" in out["reason"].lower()
    assert gh.merged is False


def test_timeout_holds():
    v = _Vercel([None, "QUEUED", "BUILDING"])
    out, gh = _run(v, timeout_polls=3)
    assert out["merged"] is False
    assert "timeout" in out["reason"].lower()
    assert gh.merged is False


def test_uses_vercel_api_not_github_checkruns():
    v = _Vercel(["READY"])
    out, gh = _run(v)
    assert out["merged"] is True
    # every build-verification call hit the Vercel deployments API, never check-runs
    assert v.calls and all("api.vercel.com/v6/deployments" in u for u in v.calls)
    assert all("check-runs" not in u for u in v.calls)
    # and the primary query is scoped by project + commit sha + team
    first = v.calls[0]
    assert "projectId=prj_x" in first
    assert "meta-githubCommitSha=abc123" in first
    assert "teamId=team_x" in first


def test_falls_back_to_branch_ref_when_sha_unmapped():
    """If the commit sha has no deployment yet, the branch ref is queried too."""
    class _RefVercel(_Vercel):
        def __call__(self, method, url, token, json_body=None):
            self.calls.append(url)
            if "meta-githubCommitSha" in url:
                return {"deployments": []}  # sha never maps
            if "meta-githubCommitRef" in url:
                return {"deployments": [{"readyState": "READY"}]}
            return {"deployments": []}

    out, gh = _run(_RefVercel([]))
    assert out["merged"] is True
    assert gh.merged is True


def test_holds_when_vercel_token_missing(monkeypatch):
    monkeypatch.delenv("VERCEL_API_TOKEN", raising=False)
    gh = _GH()
    out = sg.merge_when_green(
        "salesdroid/automotive-intelligence",
        "https://github.com/salesdroid/automotive-intelligence/pull/30",
        "ghtok", http=gh, vercel_http=_Vercel(["READY"]),
        vercel_token="", poll_sleep=0,
    )
    assert out["merged"] is False
    assert "vercel_api_token" in out["reason"].lower()
    assert gh.merged is False


# --- 2026-11-11: Vercel bot PR-comment fallback when the Vercel API cannot answer ---
# VERCEL_API_TOKEN died in the 10-21 breach rotation; every auto-merge since held.
import base64 as _b64
import json as _json


def _vc_body(project_id="prj_x", status="DEPLOYED"):
    payload = _b64.b64encode(_json.dumps({"projects": [
        {"name": "site", "projectId": project_id, "nextCommitStatus": status}]}).encode()).decode()
    return f"[vc]: #abc=:{payload}\nThe latest updates on your projects."


class _GHWithComments(_GH):
    """GitHub http that also serves the PR's commits and issue comments."""

    def __init__(self, comments, head_at="2026-11-12T03:49:23Z"):
        super().__init__()
        self.comments = comments
        self.head_at = head_at

    def __call__(self, method, url, token, json_body=None):
        if "/pulls/30/commits" in url:
            return [{"commit": {"committer": {"date": "2026-11-12T03:49:12Z"}}},
                    {"commit": {"committer": {"date": self.head_at}}}]
        if "/issues/30/comments" in url:
            return self.comments
        return super().__call__(method, url, token, json_body)


def _bot(body, updated_at="2026-11-12T03:49:55Z", login="vercel[bot]", utype="Bot"):
    return {"user": {"login": login, "type": utype}, "updated_at": updated_at, "body": body}


class _DeadVercel:
    """The revoked-token case: every Vercel API call raises."""

    def __call__(self, method, url, token, json_body=None):
        raise sg.PublishError("GET https://api.vercel.com/v6/deployments -> 403: forbidden")


def test_comment_fallback_merges_when_api_dead_and_bot_says_deployed():
    gh = _GHWithComments([_bot(_vc_body())])
    out, gh = _run(_DeadVercel(), gh=gh)
    assert out["merged"] is True
    assert out.get("gate") == "vercel_bot_comment"
    assert gh.merged is True


def test_comment_fallback_needs_two_consecutive_ready_polls():
    gh = _GHWithComments([_bot(_vc_body())])
    out, gh = _run(_DeadVercel(), gh=gh, timeout_polls=1)
    assert out["merged"] is False
    assert gh.merged is False


def test_comment_fallback_holds_on_failed_build():
    gh = _GHWithComments([_bot(_vc_body(status="FAILED"))])
    out, gh = _run(_DeadVercel(), gh=gh)
    assert out["merged"] is False
    assert "error" in out["reason"].lower()
    assert gh.merged is False


def test_comment_fallback_ignores_stale_comment_older_than_head_commit():
    gh = _GHWithComments([_bot(_vc_body(), updated_at="2026-11-12T03:49:20Z")])
    out, gh = _run(_DeadVercel(), gh=gh, timeout_polls=3)
    assert out["merged"] is False
    assert gh.merged is False


def test_comment_fallback_ignores_wrong_project_and_non_bot_author():
    for comments in (
        [_bot(_vc_body(project_id="prj_other"))],
        [_bot(_vc_body(), login="someone", utype="User")],
        [_bot(_vc_body(), login="vercel[bot]", utype="User")],
        [_bot("no payload here")],
        [],
    ):
        gh = _GHWithComments(comments)
        out, gh = _run(_DeadVercel(), gh=gh, timeout_polls=3)
        assert out["merged"] is False, comments
        assert gh.merged is False


def test_comment_fallback_not_used_while_vercel_api_answers():
    """A healthy API that says BUILDING must not be overridden by the comment."""
    gh = _GHWithComments([_bot(_vc_body())])
    out, gh = _run(_Vercel(["BUILDING", "BUILDING", "BUILDING"]), gh=gh, timeout_polls=3)
    assert out["merged"] is False
    assert gh.merged is False


def test_comment_fallback_works_with_no_token_when_project_id_is_set(monkeypatch):
    monkeypatch.delenv("VERCEL_API_TOKEN", raising=False)
    gh = _GHWithComments([_bot(_vc_body())])
    out = sg.merge_when_green(
        "salesdroid/automotive-intelligence",
        "https://github.com/salesdroid/automotive-intelligence/pull/30",
        "ghtok", http=gh, vercel_http=_DeadVercel(), vercel_token="",
        vercel_project_id="prj_x", poll_sleep=0,
    )
    assert out["merged"] is True
    assert gh.merged is True
