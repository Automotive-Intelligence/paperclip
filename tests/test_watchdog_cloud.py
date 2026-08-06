"""Cloud-coverage checks: Vercel production deployments + GitHub workflow
conclusions. Both fail CLOSED on a dead token (coverage loss is an anomaly)."""
import os
from unittest import mock

from services import watchdog


class _Resp:
    def __init__(self, code=200, payload=None, headers=None):
        self.status_code = code
        self.ok = 200 <= code < 300
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _vc_cfg():
    return {"vercel": {"team_id": "team_x", "scan_limit": 50}}


def _dep(name, state, target="production", url="dpl.vercel.app"):
    return {"name": name, "state": state, "target": target, "url": url}


def _vc_env():
    return mock.patch.dict(os.environ, {"VERCEL_API_TOKEN": "tok"})


def test_vercel_disabled_without_config_or_token():
    with mock.patch.object(watchdog.requests, "get") as seam:
        with mock.patch.dict(os.environ, {"VERCEL_API_TOKEN": ""}):
            assert watchdog._check_vercel_deployments(_vc_cfg()) == []
        with _vc_env():
            assert watchdog._check_vercel_deployments({"vercel": {}}) == []
        seam.assert_not_called()


def test_vercel_blocked_production_flags():
    """The bookd-clarity incident: newest production deployment BLOCKED."""
    payload = {"deployments": [_dep("bookd-clarity", "BLOCKED")]}
    with _vc_env(), mock.patch.object(watchdog.requests, "get",
                                      return_value=_Resp(200, payload)):
        out = watchdog._check_vercel_deployments(_vc_cfg())
    assert [a.fingerprint for a in out] == ["vercel-deploy-blocked-bookd-clarity"]
    assert watchdog._runbook(out[0].fingerprint)


def test_vercel_newer_ready_masks_older_failure():
    """A READY deploy newer than the failure (list is newest-first) = healthy."""
    payload = {"deployments": [_dep("bookd-clarity", "READY"),
                               _dep("bookd-clarity", "BLOCKED")]}
    with _vc_env(), mock.patch.object(watchdog.requests, "get",
                                      return_value=_Resp(200, payload)):
        assert watchdog._check_vercel_deployments(_vc_cfg()) == []


def test_vercel_preview_failures_ignored():
    payload = {"deployments": [_dep("bookd-clarity", "ERROR", target=None)]}
    with _vc_env(), mock.patch.object(watchdog.requests, "get",
                                      return_value=_Resp(200, payload)):
        assert watchdog._check_vercel_deployments(_vc_cfg()) == []


def test_vercel_dead_token_is_an_anomaly_not_a_skip():
    with _vc_env(), mock.patch.object(watchdog.requests, "get",
                                      return_value=_Resp(401)):
        out = watchdog._check_vercel_deployments(_vc_cfg())
    assert [a.fingerprint for a in out] == ["vercel-watch-auth-dead"]


def test_vercel_network_error_is_skip():
    with _vc_env(), mock.patch.object(watchdog.requests, "get",
                                      side_effect=watchdog.requests.ConnectionError("x")):
        assert watchdog._check_vercel_deployments(_vc_cfg()) == []


def test_vercel_multiple_projects_each_judged_on_newest():
    payload = {"deployments": [
        _dep("site-a", "READY"), _dep("site-b", "ERROR"),
        _dep("site-a", "ERROR"), _dep("site-c", "CANCELED")]}
    with _vc_env(), mock.patch.object(watchdog.requests, "get",
                                      return_value=_Resp(200, payload)):
        out = watchdog._check_vercel_deployments(_vc_cfg())
    assert [a.fingerprint for a in out] == [
        "vercel-deploy-error-site-b", "vercel-deploy-canceled-site-c"]


# ---- github workflows -------------------------------------------------------

def _gw_cfg():
    return {"github_workflows": {"repos": {"o/r": ["a.yml", "b.yml"]}}}


def _runs(conclusion):
    return {"workflow_runs": [{"conclusion": conclusion, "html_url": "http://run"}]}


def test_gh_disabled_without_repos():
    with mock.patch.object(watchdog.requests, "get") as seam:
        assert watchdog._check_github_workflows({"github_workflows": {}}) == []
        seam.assert_not_called()


def test_gh_latest_failure_flags_with_runbook():
    seq = iter([_Resp(200, _runs("failure")), _Resp(200, _runs("success"))])
    with mock.patch.object(watchdog.requests, "get", side_effect=lambda *a, **k: next(seq)):
        out = watchdog._check_github_workflows(_gw_cfg())
    assert [a.fingerprint for a in out] == ["gh-workflow-failing-o/r-a.yml"]
    assert watchdog._runbook(out[0].fingerprint)


def test_gh_success_and_cancelled_and_empty_are_quiet():
    seq = iter([_Resp(200, _runs("success")),
                _Resp(200, {"workflow_runs": []})])
    with mock.patch.object(watchdog.requests, "get", side_effect=lambda *a, **k: next(seq)):
        assert watchdog._check_github_workflows(_gw_cfg()) == []
    seq = iter([_Resp(200, _runs("cancelled")), _Resp(200, _runs("success"))])
    with mock.patch.object(watchdog.requests, "get", side_effect=lambda *a, **k: next(seq)):
        assert watchdog._check_github_workflows(_gw_cfg()) == []


def test_gh_dead_token_is_one_anomaly():
    with mock.patch.object(watchdog.requests, "get", return_value=_Resp(401)):
        out = watchdog._check_github_workflows(_gw_cfg())
    assert [a.fingerprint for a in out] == ["gh-watch-auth-dead"]


def test_gh_403_without_scope_is_coverage_dark():
    """The first live run: SLIPSTREAM_GH_TOKEN 403s on Actions endpoints. That
    must alert (coverage dark), never skip silently."""
    with mock.patch.object(watchdog.requests, "get", return_value=_Resp(403)):
        out = watchdog._check_github_workflows(_gw_cfg())
    assert [a.fingerprint for a in out] == ["gh-watch-auth-dead"]


def test_gh_rate_limit_403_is_transient_skip():
    resp = _Resp(403, headers={"x-ratelimit-remaining": "0"})
    with mock.patch.object(watchdog.requests, "get", return_value=resp):
        assert watchdog._check_github_workflows(_gw_cfg()) == []


def test_cloud_checks_registered():
    assert watchdog._check_vercel_deployments in watchdog._CHECKS
    assert watchdog._check_github_workflows in watchdog._CHECKS
