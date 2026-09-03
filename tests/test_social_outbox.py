"""Export mode: produce + gate as normal, then write a paste-ready pack and make
ZERO Zernio calls. The last assertion in most of these is the point -- a pack
that still uploaded media or called the loader would bill exactly as before."""
from unittest import mock

import pytest

from services import social_outbox as so
from services.studio_social_publish import build_export_items


def _items():
    return [{"platform": "facebook", "when": "2026-09-07 09:00 CT",
             "text": "The call you missed is the job you lost.", "image_file": "p1.png"},
            {"platform": "instagram", "when": "2026-09-08 11:00 CT",
             "text": "Second post body.", "image_url": "https://x.co/hero.png"}]


def test_pack_is_paste_ready_and_ordered_by_time():
    md = so.render_pack("Agent Empire", "week-of-2026-09-07", _items())
    assert md.index("FACEBOOK") < md.index("INSTAGRAM")      # chronological
    assert "The call you missed is the job you lost." in md
    assert "p1.png" in md and "https://x.co/hero.png" in md
    assert md.count("```") == 4                               # one fenced block per post


def test_pack_says_plainly_that_nothing_was_sent():
    md = so.render_pack("Book'd", "week-of-2026-09-07", _items())
    assert "BY HAND" in md and "Zernio" in md


def test_text_only_post_is_labelled_not_left_blank():
    md = so.render_pack("WD", "l", [{"platform": "x", "when": "w", "text": "t"}])
    assert "Image: none (text only)" in md


def test_export_writes_images_and_posts_and_calls_no_zernio():
    puts = []
    with mock.patch.object(so, "_put", side_effect=lambda p, r, m, t: puts.append(p)):
        r = so.export("agentempire", "Agent Empire", "week-of-2026-09-07",
                      _items(), "tok", images={"p1.png": b"\x89PNG"})
    assert r["ok"] and r["posts"] == 2
    assert any(p.endswith("/p1.png") for p in puts)
    assert any(p.endswith("/POSTS.md") for p in puts)
    assert all(p.startswith("social_outbox/agentempire/week-of-2026-09-07/") for p in puts)


def test_a_failed_commit_raises_rather_than_reporting_a_written_pack():
    bad = mock.Mock(ok=False, status_code=422, text="nope")
    with mock.patch.object(so.requests, "get", return_value=mock.Mock(ok=False)), \
         mock.patch.object(so.requests, "put", return_value=bad):
        with pytest.raises(RuntimeError, match="outbox commit"):
            so.export("wd", "WD", "l", _items(), "tok")


# ------------------------------------------------------------- item building
_CFG = {"display_name": "Agent Empire", "business_key": "agentempire"}
_POSTS = [{"key": "p1", "platforms": {"facebook": "a", "instagram": "b"}}]


def test_export_needs_no_connected_account():
    # build_jobs SKIPS a platform with no account id; by hand he can post anywhere
    # he has a login, so export must not inherit that gate.
    items, skips = build_export_items(_CFG, _POSTS, "2026-09-07", {"agent empire": {}})
    assert len(items) == 2 and not skips


def test_missing_stagger_slot_falls_back_instead_of_dropping_the_post():
    items, _ = build_export_items(_CFG, _POSTS, "2026-09-07", {})
    assert all(i["when"].endswith("09:00 CT") for i in items)


def test_x_length_guard_is_kept_because_a_long_post_breaks_by_hand_too():
    posts = [{"key": "p1", "platforms": {"x": "y" * 400}}]
    items, skips = build_export_items(_CFG, posts, "2026-09-07", {})
    assert not items and skips and "over" in skips[0][2]


# ------------------------------------------------- the engine seams spend nothing
def test_weekly_engine_export_makes_no_zernio_call_at_all():
    from services import studio_social_engine as e
    cfg = {**_CFG, "social_mode": "export", "zernio_profile": "Agent Empire"}
    produced = {"kept": _POSTS, "media_bytes": {"p1": b"\x89PNG"}, "dropped": [], "stamps": []}
    with mock.patch.object(e, "resolve_accounts") as racc, \
         mock.patch.object(e, "_upload_media") as up, \
         mock.patch.object(e, "run_social_load") as load, \
         mock.patch("services.social_outbox.export",
                    return_value={"folder": "social_outbox/agentempire/week-of-2026-09-07",
                                  "files": [], "posts": 2, "ok": True}) as exp, \
         mock.patch.dict("os.environ", {"SLIPSTREAM_GH_TOKEN": "tok"}):
        r = e.schedule_brand(cfg, produced, "2026-09-07", [], [], "cid", commit=True)
    assert r["exported"] and not r["held"]
    racc.assert_not_called()      # no account lookup
    up.assert_not_called()        # no media upload  <- the actual spend
    load.assert_not_called()      # no schedule
    assert exp.call_args.kwargs["images"] == {"p1.png": b"\x89PNG"}


def test_weekly_engine_holds_loudly_when_the_token_is_missing():
    from services import studio_social_engine as e
    cfg = {**_CFG, "social_mode": "export", "zernio_profile": "Agent Empire"}
    produced = {"kept": _POSTS, "media_bytes": {}, "dropped": [], "stamps": []}
    with mock.patch.dict("os.environ", {"SLIPSTREAM_GH_TOKEN": "", "GITHUB_TOKEN": ""}), \
         mock.patch.object(e, "run_social_load") as load:
        r = e.schedule_brand(cfg, produced, "2026-09-07", [], [], "cid", commit=True)
    # Fails CLOSED: a pack that cannot be written must not look like a clean run.
    assert r["held"] and "NOT written" in r["reason"]
    load.assert_not_called()


def test_blog_engine_export_skips_the_loader():
    from services import slipstream_engine as s
    cfg = {"business_key": "bookd", "display_name": "Book'd", "social_mode": "export",
           "format": "tsx_post",
           "zernio_accounts": {"facebook": "fb1", "instagram": "ig1"}}
    post = {"social": {"facebook": "fb copy", "instagram": "ig copy"}}
    with mock.patch("services.social_load_service.run_social_load") as load, \
         mock.patch("services.social_outbox.export",
                    return_value={"folder": "f", "files": [], "posts": 2, "ok": True}) as exp, \
         mock.patch.dict("os.environ", {"SLIPSTREAM_GH_TOKEN": "tok"}):
        r = s._distribute_social(cfg, post, "my-slug", "https://bookd.cx/blog/my-slug")
    assert r["ok"] and r["exported"]
    load.assert_not_called()
    items = exp.call_args[0][3]
    assert {i["platform"] for i in items} == {"facebook", "instagram"}
    # tsx_post serves hero images from /img/, not /blog/ -- a wrong path here is a
    # dead image link in a pack he is posting by hand.
    assert all("/img/my-slug-hero.png" in i["image_url"] for i in items)
