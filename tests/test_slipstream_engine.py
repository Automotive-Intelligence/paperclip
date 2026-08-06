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
