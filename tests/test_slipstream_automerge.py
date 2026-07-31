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
