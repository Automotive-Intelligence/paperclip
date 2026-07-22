from unittest import mock

from services import social_load_service as sls

_JOB = {
    "brand": "autointelligence", "platform": "linkedin",
    "content": "hello https://automotiveintelligence.io/blog/x",
    "scheduled_for": "2026-07-22T07:45:00", "content_id": "x",
    "entry_point": "blog_engine",
}


def test_empty_jobs_rejected():
    out = sls.run_social_load([])
    assert out["ok"] is False
    assert "non-empty" in out["error"]


def test_missing_required_field_is_soft_error():
    bad = dict(_JOB)
    del bad["content_id"]
    out = sls.run_social_load([bad])
    assert out["ok"] is False
    assert "error" in out


def test_unknown_keys_filtered_and_load_jobs_called():
    from tools.social_load import PostJob
    seen = {}

    def _fake_load_jobs(jobs, commit=False, allow_stack=False, **k):
        seen["n"] = len(jobs)
        seen["commit"] = commit
        seen["brand"] = jobs[0].brand
        return [{"job": jobs[0], "action": "scheduled", "detail": "ok"}]

    job = dict(_JOB, bogus_key="ignore me")
    with mock.patch.object(sls, "load_jobs", side_effect=_fake_load_jobs):
        out = sls.run_social_load([job], commit=True)
    assert seen["n"] == 1
    assert seen["commit"] is True
    assert seen["brand"] == "autointelligence"
    assert out["ok"] is True
    assert out["results"][0]["action"] == "scheduled"
    assert out["results"][0]["brand"] == "autointelligence"


def test_skipped_media_is_surfaced_in_summary():
    # A dead-media job is skipped (not error/conflict), so the run stays ok=True
    # but the skip is surfaced with url + content_id + reason for visibility.
    class _J:
        brand = "worshipdigital"
        platform = "twitter"

    def _fake(jobs, **k):
        return [
            {"job": _J(), "action": "scheduled", "detail": {"_id": "zp1"}},
            {"job": _J(), "action": "skipped",
             "detail": {"reason": "media_unreachable",
                        "url": "https://worshipdigital.co/blog/dead-hero.png",
                        "content_id": "hero9", "message": "..."}},
        ]

    with mock.patch.object(sls, "load_jobs", side_effect=_fake):
        out = sls.run_social_load([_JOB, _JOB])
    assert out["ok"] is True                      # skip does not fail the whole run
    assert out["counts"]["skipped"] == 1
    assert len(out["skipped"]) == 1
    assert out["skipped"][0]["content_id"] == "hero9"
    assert out["skipped"][0]["url"] == "https://worshipdigital.co/blog/dead-hero.png"
    assert out["skipped"][0]["reason"] == "media_unreachable"


def test_error_action_makes_not_ok():
    class _J:
        brand = "autointelligence"
        platform = "x"

    def _fake(jobs, **k):
        return [{"job": _J(), "action": "error", "detail": "zernio 500"}]

    with mock.patch.object(sls, "load_jobs", side_effect=_fake):
        out = sls.run_social_load([_JOB])
    assert out["ok"] is False
    assert out["counts"]["error"] == 1
