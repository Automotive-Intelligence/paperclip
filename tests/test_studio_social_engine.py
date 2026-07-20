"""Engine orchestrator: dry-run, idempotency, staging, Iris-drop hold, loud verify."""
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest import mock

from services import studio_social_engine as E

_PROFILES = [{"name": "Automotive Intelligence", "_id": "prof_avi"}]
_ACCTS = [{"platform": p, "_id": f"a_{p}", "profileId": "prof_avi"}
          for p in ("twitter", "linkedin", "facebook", "instagram")]


def _one_post():
    return [{"key": "p1", "theme": "lead response", "image_prompt": "abstract scene",
             "platforms": {"linkedin": "LI", "x": "X", "facebook": "FB", "instagram": "IG"}}]


def _patch(stack, *, iris_pass=True, load=None, commit_spy=None):
    """Patch every external seam of the engine to hermetic fakes."""
    p = stack.enter_context
    p(mock.patch.object(E, "generate_posts", lambda cfg, wk, n: _one_post()))
    p(mock.patch.object(E, "_hero_image", lambda prompt, bk: b"PNG"))
    p(mock.patch.object(E, "iris_review", lambda png, cfg: {"passed": iris_pass, "reason": "r"}))
    p(mock.patch.object(E, "scrutineer_review", lambda posts, cfg: {"posts": posts, "notes": []}))
    p(mock.patch.object(E, "conversion_review", lambda posts, cfg: {"posts": posts, "notes": []}))
    p(mock.patch.object(E, "_zernio_profiles_accounts", lambda: (_PROFILES, _ACCTS)))
    p(mock.patch.object(E, "_upload_media", lambda png, name: f"https://media/{name}"))
    p(mock.patch.object(E, "run_social_load",
                        load or (lambda jobs, commit=False: {
                            "ok": True, "counts": {"dry-run": len(jobs)},
                            "results": [{"brand": j["brand"], "platform": j["platform"],
                                         "action": "dry-run"} for j in jobs]})))


def test_dry_run_produces_gated_scheduled_batch():
    with ExitStack() as s:
        _patch(s)
        r = E.run_week(brands=["automotive_intelligence"], commit=False, token="tok")
    assert r["ok"] is True
    assert r["staged"] is False                    # dry-run never commits
    assert r["brands"]["automotive_intelligence"]["held"] is False
    assert r["brands"]["automotive_intelligence"]["kept"] == 1


def test_commit_stages_deliverable_folder():
    commit_spy = mock.MagicMock()
    with ExitStack() as s:
        _patch(s, load=lambda jobs, commit=False: {"ok": True, "counts": {"scheduled": len(jobs)},
               "results": [{"brand": j["brand"], "platform": j["platform"],
                            "action": "scheduled"} for j in jobs]})
        s.enter_context(mock.patch.object(E, "week_already_published", lambda wk, tok: False))
        s.enter_context(mock.patch.object(E, "_next_deliverable_number", lambda tok: 143))
        s.enter_context(mock.patch.object(E, "_commit_files_to_main", commit_spy))
        r = E.run_week(brands=["automotive_intelligence"], commit=True, token="tok")
    assert r["ok"] is True and r["staged"] is True
    commit_spy.assert_called_once()
    files = commit_spy.call_args.args[0]
    assert any("143_studio_weekly_" in path for path in files)
    assert any("RECEIPT_" in path for path in files)


def test_idempotency_refuses_double_publish():
    with ExitStack() as s:
        _patch(s)
        s.enter_context(mock.patch.object(E, "week_already_published", lambda wk, tok: True))
        r = E.run_week(brands=["automotive_intelligence"], commit=True, token="tok")
    assert r.get("skipped") is True


def test_force_bypasses_idempotency():
    commit_spy = mock.MagicMock()
    with ExitStack() as s:
        _patch(s, load=lambda jobs, commit=False: {"ok": True, "counts": {"scheduled": len(jobs)},
               "results": [{"brand": j["brand"], "platform": j["platform"],
                            "action": "scheduled"} for j in jobs]})
        # Week IS already published, but force=True must proceed anyway (re-run to
        # un-dark a brand after a fix), not skip.
        s.enter_context(mock.patch.object(E, "week_already_published", lambda wk, tok: True))
        s.enter_context(mock.patch.object(E, "_next_deliverable_number", lambda tok: 145))
        s.enter_context(mock.patch.object(E, "_commit_files_to_main", commit_spy))
        r = E.run_week(brands=["automotive_intelligence"], commit=True, force=True, token="tok")
    assert not r.get("skipped")
    assert r["ok"] is True and r["staged"] is True


def test_iris_drops_all_posts_holds_brand_and_flags_loud():
    with ExitStack() as s:
        _patch(s, iris_pass=False)
        r = E.run_week(brands=["automotive_intelligence"], commit=False, token="tok")
    assert r["brands"]["automotive_intelligence"]["held"] is True
    assert r["ok"] is False                          # nothing landed -> loud, not silent
    assert r["problems"]


def test_missing_token_is_loud():
    r = E.run_week(brands=["automotive_intelligence"], commit=False, token="")
    assert r["ok"] is False and "SLIPSTREAM_GH_TOKEN" in r["error"]


def test_hero_image_passes_brand_references_straight_through():
    # The WD-yield-0 fix: references_for's list of URL STRINGS must reach
    # blog_image as reference_image_urls unchanged (no dict extraction).
    import services.blog_image as BI
    import tools.fal_assets as FA
    captured = {}

    def fake_blog_image(prompt, *, business_key="", aspect_ratio="", pro=False,
                        reference_image_urls=None):
        captured["refs"] = reference_image_urls
        return {"ok": True, "urls": ["https://img/x.png"]}

    class _R:
        content = b"PNG"

        def raise_for_status(self):
            return None

    with mock.patch.object(FA, "references_for", lambda bk: ["u1", "u2", "u3", "u4"]), \
         mock.patch.object(BI, "blog_image", fake_blog_image), \
         mock.patch.object(E.requests, "get", lambda url, timeout=0: _R()):
        out = E._hero_image("a scene", "worshipdigital")
    assert out == b"PNG"
    assert captured["refs"] == ["u1", "u2", "u3", "u4"]


def test_upcoming_monday_is_a_future_monday():
    # 2026-07-20 is a Monday; the upcoming Monday from it is 2026-07-27 (never today).
    got = E.upcoming_monday(datetime(2026, 7, 20, 12, tzinfo=timezone.utc))
    assert got == "2026-07-27"
    d = datetime.fromisoformat(got)
    assert d.weekday() == 0
