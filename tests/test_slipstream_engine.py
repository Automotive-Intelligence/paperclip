from unittest import mock

from services import slipstream_engine as se

_POST = {
    "title": "T", "description": "D", "slug": "my-post",
    "body_mdx": "<AnswerFirst>a</AnswerFirst>",
    "image_prompts": [{"name": "hero", "prompt": "h"}, {"name": "gap", "prompt": "g"}],
    "social": {"linkedin": "li", "x": "x"},
}
_CFG = {"brand_key": "autointelligence", "repo": "salesdroid/automotive-intelligence",
        "domain": "automotiveintelligence.io",
        "blog_dir": "src/content/blog", "business_key": "autointelligence",
        "money_pages": ["/services"], "voice": "diagnostic"}


def _patch_all(violations, publish_url="https://gh/pull/1"):
    return (
        mock.patch.object(se, "_brand_cfg", return_value=_CFG),
        mock.patch.object(se, "generate_post", return_value=_POST),
        mock.patch.object(se, "generate_images", return_value={"hero": b"H", "gap": b"G"}),
        mock.patch.object(se, "assemble_mdx", return_value=("MDX", violations)),
        mock.patch.object(se, "publish_post", return_value=publish_url),
    )


def test_clean_run_publishes_and_returns_pr():
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3] as _asm, ps[4] as pub:
        out = se.run_brand("autointelligence", topic="a topic", token="tok", date_str="2026-07-19", auto_merge=False)
    assert out["ok"] is True
    assert out["pr_url"] == "https://gh/pull/1"
    assert out["slug"] == "my-post"
    # publish got the mdx + both images as files under the right paths
    files = pub.call_args.kwargs["files"] if pub.call_args.kwargs.get("files") is not None else pub.call_args.args[2]
    paths = list(files.keys())
    assert "src/content/blog/my-post.mdx" in paths
    assert "public/blog/my-post-hero.png" in paths
    assert "public/blog/my-post-gap.png" in paths


def test_gate_violation_holds_and_does_not_publish():
    ps = _patch_all(["missing pullquote"])
    with ps[0], ps[1], ps[2], ps[3], ps[4] as pub:
        out = se.run_brand("autointelligence", topic="a topic", token="tok", date_str="2026-07-19", auto_merge=False)
    assert out["ok"] is False
    assert out["held"] is True
    assert "missing pullquote" in out["violations"]
    pub.assert_not_called()


def test_missing_token_holds():
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4] as pub:
        out = se.run_brand("autointelligence", topic="t", token="", date_str="2026-07-19", auto_merge=False)
    assert out["ok"] is False
    pub.assert_not_called()


def test_auto_merge_publishes_and_distributes():
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "merge_when_green", return_value={"merged": True, "pr_url": "https://gh/pull/1"}) as mw, \
         mock.patch.object(se, "_distribute_social", return_value={"ok": True}) as ds, \
         mock.patch.object(se, "_checkoff_topic", return_value=True):
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-07-19")
    assert out["published"] is True
    assert out["live_url"] == "https://automotiveintelligence.io/blog/my-post"
    mw.assert_called_once()
    ds.assert_called_once()


def test_red_build_holds_pr_no_social():
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "merge_when_green", return_value={"merged": False, "reason": "vercel build failure"}), \
         mock.patch.object(se, "_distribute_social") as ds, \
         mock.patch.object(se, "_checkoff_topic", return_value=False):
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-07-19")
    assert out["published"] is False
    assert "held" in out["note"].lower()
    ds.assert_not_called()


def test_exhausted_queue_holds_with_distinct_reason():
    """An exhausted queue (no unchecked topic; _next_topic raises QueueExhausted)
    must HOLD with a DISTINCT reason='queue_exhausted', not a generic produce fail,
    so the scheduler/watchdog can surface it. No topic passed -> the engine reads
    the queue. This is the 2026-07-31 BAE-class silent hold made visible."""
    with mock.patch.object(se, "_brand_cfg", return_value=_CFG), \
         mock.patch.object(se, "_next_topic", side_effect=se.QueueExhausted("queue exhausted: no unchecked topic")), \
         mock.patch.object(se, "publish_post") as pub:
        out = se.run_brand("autointelligence", token="tok", date_str="2026-07-19")
    assert out["ok"] is False
    assert out["held"] is True
    assert out["reason"] == "queue_exhausted"
    pub.assert_not_called()


def test_checkoff_invoked_with_topic_after_merge():
    """After a green auto-merge, run_brand marks the SHIPPED topic checked so the
    queue advances (no duplicate republish next run). Assert the check-off gets the
    exact topic + derived live_url."""
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "merge_when_green", return_value={"merged": True, "pr_url": "https://gh/pull/1"}), \
         mock.patch.object(se, "_distribute_social", return_value={"ok": True}), \
         mock.patch.object(se, "_checkoff_topic", return_value=True) as co:
        out = se.run_brand("autointelligence", topic="the shipped topic", token="tok", date_str="2026-07-19")
    assert out["topic_checked_off"] is True
    co.assert_called_once_with(_CFG, "the shipped topic",
                               "https://automotiveintelligence.io/blog/my-post", "tok")


def test_social_distribution_killswitch(monkeypatch):
    """SLIPSTREAM_SOCIAL_DISTRIBUTE=0 pauses social auto-fire WITHOUT touching the
    publish path: the blog still merges + the topic still checks off, but no social
    job is scheduled and the receipt says so."""
    monkeypatch.setenv("SLIPSTREAM_SOCIAL_DISTRIBUTE", "0")
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "merge_when_green", return_value={"merged": True, "pr_url": "https://gh/pull/1"}), \
         mock.patch.object(se, "_checkoff_topic", return_value=True), \
         mock.patch("services.social_load_service.run_social_load") as rsl:
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-07-19")
    assert out["published"] is True
    assert out["social"]["disabled"] is True
    rsl.assert_not_called()


# --- social account routing (2026-08-06) -------------------------------------
# The engine used to build social jobs with no account_id. tools/zernio treats a
# missing account id as "post to EVERY connected account on this platform", so
# every auto-published blog cross-posted onto every other brand's feed, including
# Book'd (Ryan's) and Michael's personal founder profile. Same defect class as the
# 2026-07-19 incident, but automated.

def _social_cfg(accounts):
    return {**_CFG, "zernio_accounts": accounts}


def test_social_jobs_carry_an_explicit_account_id():
    cfg = _social_cfg({"linkedin": "acct_li", "x": "acct_x"})
    with mock.patch("services.social_load_service.run_social_load",
                    return_value={"ok": True, "counts": {"scheduled": 2}}) as rsl:
        se._distribute_social(cfg, _POST, "my-post", "https://d/blog/my-post")
    jobs = rsl.call_args[0][0]
    assert [j["account_id"] for j in jobs] == ["acct_li", "acct_x"]
    assert all(j["account_id"] for j in jobs)


def test_platform_without_an_account_is_skipped_not_fanned_out():
    """AIPG and BAE genuinely have no LinkedIn or X account. Skipping is correct;
    passing None through is what cross-posts them onto other brands."""
    cfg = _social_cfg({"linkedin": "acct_li"})   # no x
    with mock.patch("services.social_load_service.run_social_load",
                    return_value={"ok": True, "counts": {"scheduled": 1}}) as rsl:
        se._distribute_social(cfg, _POST, "my-post", "https://d/blog/my-post")
    jobs = rsl.call_args[0][0]
    assert [j["platform"] for j in jobs] == ["linkedin"]


def test_no_accounts_at_all_schedules_nothing():
    cfg = _social_cfg({})
    with mock.patch("services.social_load_service.run_social_load") as rsl:
        out = se._distribute_social(cfg, _POST, "my-post", "https://d/blog/my-post")
    rsl.assert_not_called()
    assert out["ok"] is False


def test_wd_config_has_accounts_and_automerge_on():
    """Regression guard for the real config: WD's auto_merge:false was what kept
    _checkoff_topic from ever running, so the queue never marked and the engine
    reproduced the same topic three times (PRs #17/#18/#19)."""
    full = se._load_cfg()
    assert full["brands"]["worshipdigital"]["auto_merge"] is True
    accounts = full["zernio_accounts"]
    assert accounts["autointelligence"]["linkedin"]
    for brand, acc in accounts.items():
        for platform, aid in acc.items():
            assert aid and isinstance(aid, str), (brand, platform)


# --- social distribution must cover the platforms a brand ACTUALLY has -------
# Book'd (facebook+instagram) and Agent Empire (facebook+instagram+youtube) have
# NEITHER linkedin nor x. The old two-platform loop silently produced zero jobs
# for them, so every blog they published reached no social audience at all.
def _post_with(social):
    return {"social": social}


def _cfg(accounts):
    return {"business_key": "bookd", "zernio_accounts": accounts}


def test_facebook_instagram_brand_still_gets_jobs(monkeypatch):
    sent = {}

    def _loader(jobs, commit=True):
        sent["jobs"] = jobs
        return {"ok": True, "counts": {"scheduled": len(jobs)}}

    monkeypatch.setattr("services.social_load_service.run_social_load", _loader)
    monkeypatch.setenv("SLIPSTREAM_SOCIAL_DISTRIBUTE", "1")
    se._distribute_social(
        _cfg({"facebook": "fb1", "instagram": "ig1"}),
        _post_with({"linkedin": "li copy", "x": "x copy",
                    "facebook": "fb copy", "instagram": "ig copy"}),
        "some-slug", "https://bookd.cx/blog/some-slug")
    plats = {j["platform"] for j in sent.get("jobs", [])}
    assert plats == {"facebook", "instagram"}, plats


def test_older_post_without_fb_ig_copy_falls_back(monkeypatch):
    """A post written before the schema change carries only linkedin/x copy;
    it must still reach a facebook/instagram-only brand."""
    sent = {}
    monkeypatch.setattr("services.social_load_service.run_social_load",
                        lambda jobs, commit=True: sent.update(jobs=jobs) or {"ok": True})
    monkeypatch.setenv("SLIPSTREAM_SOCIAL_DISTRIBUTE", "1")
    se._distribute_social(
        _cfg({"facebook": "fb1", "instagram": "ig1"}),
        _post_with({"linkedin": "li only", "x": "x only"}),
        "s", "https://bookd.cx/blog/s")
    assert {j["platform"] for j in sent.get("jobs", [])} == {"facebook", "instagram"}
    assert all(j["content"].strip() for j in sent["jobs"])


def test_unconnected_platform_is_still_skipped(monkeypatch):
    """Fail-closed behaviour must survive: no account id, no job, never a None
    that would fan the post out to every connected account."""
    sent = {}
    monkeypatch.setattr("services.social_load_service.run_social_load",
                        lambda jobs, commit=True: sent.update(jobs=jobs) or {"ok": True})
    monkeypatch.setenv("SLIPSTREAM_SOCIAL_DISTRIBUTE", "1")
    se._distribute_social(
        _cfg({"facebook": "fb1"}),
        _post_with({"linkedin": "li", "x": "x", "facebook": "fb", "instagram": "ig"}),
        "s", "https://bookd.cx/blog/s")
    assert {j["platform"] for j in sent.get("jobs", [])} == {"facebook"}
    assert all(j["account_id"] for j in sent["jobs"])


def test_held_post_never_pays_for_images():
    """The saving, asserted: a post that fails the content gate must not have bought
    any fal images. Before this, images were generated before assembly, so every held
    run threw away 3-4 paid Pro images."""
    from unittest import mock
    from services import slipstream_engine as SE

    calls = []
    with mock.patch.object(SE, "_brand_cfg", return_value={"format": "mdx", "business_key": "avi",
                                                           "blog_dir": "content/blog"}), \
         mock.patch.object(SE, "_next_topic", return_value="a topic"), \
         mock.patch.object(SE, "generate_post", return_value={"slug": "s", "image_prompts": [], "social": {}}), \
         mock.patch.object(SE, "assemble_mdx", return_value=("body", ["gate violation"])), \
         mock.patch.object(SE, "generate_images", side_effect=lambda *a, **k: calls.append(1) or {}):
        res = SE.run_brand("avi", token="t")

    assert res["held"] is True and res["violations"] == ["gate violation"]
    assert calls == [], "images were generated for a post that never shipped"


def test_passing_post_does_buy_images():
    """The other half: a clean post must still get its images, just later."""
    from unittest import mock
    from services import slipstream_engine as SE

    calls = []
    with mock.patch.object(SE, "_brand_cfg", return_value={"format": "mdx", "business_key": "avi",
                                                           "blog_dir": "content/blog"}), \
         mock.patch.object(SE, "_next_topic", return_value="a topic"), \
         mock.patch.object(SE, "generate_post", return_value={"slug": "s", "title": "T", "image_prompts": [], "social": {}}), \
         mock.patch.object(SE, "assemble_mdx", return_value=("body", [])), \
         mock.patch.object(SE, "generate_images", side_effect=lambda *a, **k: calls.append(1) or {"hero": b"x"}), \
         mock.patch.object(SE, "publish_post", return_value={"ok": True, "pr_url": "u"}), \
         mock.patch.object(SE, "merge_when_green", return_value={"ok": True, "merged": True}):
        SE.run_brand("avi", token="t", dry_run=True)

    assert calls == [1], "a passing post must still get its images"


# ---------------------------------------------------------------- cadence
# Before this, the MWF cron published EVERY enabled brand on all three runs and
# the only volume controls were "enabled: true" and "enabled: false". Slowing a
# brand meant silencing it, which would also have killed the social rail riding
# on its blog publishes.
from datetime import date as _d          # noqa: E402
from services.slipstream_engine import due_today  # noqa: E402

_MON, _TUE, _WED, _THU, _FRI, _SAT = (_d(2026, 8, 24), _d(2026, 8, 25), _d(2026, 8, 26),
                                      _d(2026, 8, 27), _d(2026, 8, 28), _d(2026, 8, 29))


def test_mwf_is_still_three_days_a_week():
    assert [due_today("mwf", x) for x in (_MON, _TUE, _WED, _THU, _FRI, _SAT)] == \
           [True, False, True, False, True, False]


def test_weekly_publishes_monday_only():
    assert [due_today("weekly", x) for x in (_MON, _WED, _FRI)] == [True, False, False]


def test_biweekly_skips_odd_iso_weeks_and_fires_on_even():
    assert _MON.isocalendar()[1] % 2 == 1 and due_today("biweekly", _MON) is False
    nxt = _d(2026, 8, 31)                      # ISO week 36, even
    assert nxt.isocalendar()[1] % 2 == 0 and due_today("biweekly", nxt) is True


def test_off_never_runs_on_the_cron():
    assert not any(due_today("off", x) for x in (_MON, _WED, _FRI))


def test_missing_or_unknown_cadence_keeps_the_old_mwf_behaviour():
    # A brand with no cadence key must not silently change volume.
    assert due_today(None, _WED) and due_today("", _WED) and due_today("nonsense", _WED)
    assert not due_today("mwf", _TUE)


def test_every_owned_brand_is_weekly_and_the_client_is_untouched():
    # 2026-10-26 (Michael): every AVO brand posts once a week. P&P is CLIENT work
    # (drafts only, Miriam-gated) and keeps its own MWF draft cadence.
    import yaml, pathlib
    c = yaml.safe_load(pathlib.Path("config/slipstream_brands.yaml").read_text())
    b = c["brands"]
    # 2026-11-09 (Michael): BAE + Book'd sites are no longer running, so those two
    # are OFF (the rotated SLIPSTREAM_GH_TOKEN has no access to their repos).
    # 2026-11-11: Equity Pulse joins as a weekly, enabled, human-merged brand.
    for k in ("worshipdigital", "bookd", "agentempire", "autointelligence", "aiphoneguy",
              "equitypulse"):
        assert b[k]["cadence"] == "weekly", k
    for k in ("worshipdigital", "autointelligence", "aiphoneguy", "equitypulse"):
        assert b[k]["enabled"] is True, f"{k} must stay enabled, just slower"
    for k in ("bookd", "agentempire"):
        assert b[k]["enabled"] is False, f"{k} is retired (2026-11-09)"
    assert "cadence" not in b["paperandpurpose"] or b["paperandpurpose"]["cadence"] == "mwf"


# ------------------------------------------------------------- url_prefix
# Equity Pulse publishes under /learn, not /blog. "/blog/" used to be assumed in
# the site root, the live URL and the social media URL, so a non-/blog brand would
# have checked off, linked and shared a 404.
_EP_CFG = {"brand_key": "equitypulse", "repo": "salesdroid/equity-pulse",
           "domain": "getequitypulse.com", "url_prefix": "learn",
           "blog_dir": "src/content/learn", "business_key": "equitypulse",
           "money_pages": ["/learn"], "voice": "asset lens"}


def _patch_ep(cfg):
    return (
        mock.patch.object(se, "_brand_cfg", return_value=cfg),
        mock.patch.object(se, "generate_post", return_value=_POST),
        mock.patch.object(se, "generate_images", return_value={"hero": b"H", "gap": b"G"}),
        mock.patch.object(se, "assemble_mdx", return_value=("MDX", [])),
        mock.patch.object(se, "publish_post", return_value="https://gh/pull/9"),
    )


def test_url_prefix_defaults_to_blog_and_normalizes_slashes():
    assert se._url_prefix({}) == "blog"
    assert se._url_prefix({"url_prefix": None}) == "blog"
    assert se._url_prefix({"url_prefix": ""}) == "blog"
    assert se._url_prefix({"url_prefix": "/learn/"}) == "learn"
    assert se._live_url({"domain": "d.com"}, "s") == "https://d.com/blog/s"
    assert se._live_url({"domain": "d.com", "url_prefix": "learn"}, "s") == "https://d.com/learn/s"
    assert se._live_url({}, "s") == ""


def test_site_root_splits_on_the_brand_prefix():
    assert se._site_root("https://d.com/blog/s") == "https://d.com"
    assert se._site_root("https://d.com/learn/s", "learn") == "https://d.com"


def test_prefixed_brand_publishes_under_its_own_route_with_images_under_blog():
    """The article goes to blog_dir and the live URL uses url_prefix, but images
    stay in public/blog/: the generator, the gate and heroImage all say /blog/."""
    ps = _patch_ep(_EP_CFG)
    with ps[0], ps[1], ps[2], ps[3], ps[4] as pub, \
         mock.patch.object(se, "merge_when_green", return_value={"merged": True}), \
         mock.patch.object(se, "_distribute_social", return_value={"ok": False}) as ds, \
         mock.patch.object(se, "_checkoff_topic", return_value=True) as co:
        out = se.run_brand("equitypulse", topic="t", token="tok", date_str="2026-11-16")
    assert out["live_url"] == "https://getequitypulse.com/learn/my-post"
    co.assert_called_once_with(_EP_CFG, "t", "https://getequitypulse.com/learn/my-post", "tok")
    assert ds.call_args.args[3] == "https://getequitypulse.com/learn/my-post"
    paths = list(pub.call_args.args[2].keys())
    assert "src/content/learn/my-post.mdx" in paths
    assert "public/blog/my-post-hero.png" in paths
    assert not any(p.startswith("public/learn/") for p in paths)


def test_social_media_url_uses_blog_image_dir_for_a_prefixed_brand():
    cfg = {**_EP_CFG, "zernio_accounts": {"linkedin": "acct_li"}}
    with mock.patch("services.social_load_service.run_social_load",
                    return_value={"ok": True, "counts": {"scheduled": 1}}) as rsl:
        se._distribute_social(cfg, _POST, "my-post", "https://getequitypulse.com/learn/my-post")
    job = rsl.call_args[0][0][0]
    assert job["media_urls"] == ["https://getequitypulse.com/blog/my-post-hero.png"]
    assert job["content"].endswith("https://getequitypulse.com/learn/my-post")


def test_config_auto_merge_false_opens_pr_only_and_still_advances_the_queue():
    """EP is human-merged by config. The engine must never merge or fire social,
    but it MUST check the topic off (against the PR) or the weekly cron reopens a
    PR for the same topic every Monday (the WD #17/#18/#19 failure)."""
    cfg = {**_EP_CFG, "auto_merge": False}
    ps = _patch_ep(cfg)
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "merge_when_green") as mw, \
         mock.patch.object(se, "_distribute_social") as ds, \
         mock.patch.object(se, "_checkoff_topic", return_value=True) as co:
        out = se.run_brand("equitypulse", topic="when to trade in a car", token="tok",
                           date_str="2026-11-16")
    mw.assert_not_called()
    ds.assert_not_called()
    assert out["published"] is False and out["pr_url"] == "https://gh/pull/9"
    assert "human merges" in out["note"]
    co.assert_called_once_with(cfg, "when to trade in a car", "PR: https://gh/pull/9", "tok")
    assert out["topic_checked_off"] is True
    assert out["live_url_after_merge"] == "https://getequitypulse.com/learn/my-post"


def test_caller_auto_merge_false_does_not_touch_the_queue():
    """A supervised one-off (caller passes auto_merge=False on an auto-merge brand)
    must stay repeatable: no check-off."""
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "_checkoff_topic") as co:
        out = se.run_brand("autointelligence", topic="t", token="tok",
                           date_str="2026-07-19", auto_merge=False)
    co.assert_not_called()
    assert "topic_checked_off" not in out


def test_equitypulse_real_config():
    """Guard the real yaml: /learn route, human-merged, and NO social accounts, so
    an EP article can never fan out to (or cross-post onto) any feed."""
    se._load_cfg.cache_clear()
    cfg = se._brand_cfg("equitypulse")
    assert cfg["repo"] == "salesdroid/equity-pulse"
    assert cfg["domain"] == "getequitypulse.com"
    assert cfg["url_prefix"] == "learn" and cfg["blog_dir"] == "src/content/learn"
    assert cfg["auto_merge"] is False
    assert cfg.get("format", "mdx") == "mdx"
    assert cfg["queue_repo"] == "salesdroid/avo-telemetry"
    assert cfg["queue_path"] == "scripts/blog_queues/equitypulse_topics.md"
    assert cfg["zernio_accounts"] == {}
    assert "equitypulse" not in se._load_cfg()["zernio_accounts"]
    assert "\u2014" not in cfg["voice"]
    with mock.patch("services.social_load_service.run_social_load") as rsl:
        out = se._distribute_social(cfg, {"social": {"linkedin": "a", "x": "b", "facebook": "c",
                                                     "instagram": "d"}},
                                    "s", "https://getequitypulse.com/learn/s")
    rsl.assert_not_called()
    assert out == {"ok": False, "note": "no social drafts"}


def test_equitypulse_has_an_image_style():
    from tools.image_gen import BRAND_PROMPT_STYLES
    style = BRAND_PROMPT_STYLES["equitypulse"]
    assert "#10B981" in style and "navy" in style and "NO faces" in style
