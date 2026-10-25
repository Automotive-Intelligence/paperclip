"""Social must not be scheduled until the merged article and its hero image are
LIVE. Before this, the loader ran seconds after the merge, the image 404'd on the
not-yet-deployed site, and every post was skipped as media_unreachable."""
from unittest import mock

from services import slipstream_engine as se

CFG = {"business_key": "aiphoneguy", "zernio_accounts": {"facebook": "fb1", "instagram": "ig1"}}
POST = {"social": {"facebook": "fb copy", "instagram": "ig copy"}}
LIVE = "https://theaiphoneguy.com/blog/some-slug"


def test_wait_until_live_polls_until_200():
    answers = {"a": iter([False, False, True])}
    sleeps = []
    out = se._wait_until_live(["a"], timeout_s=100, poll_s=5,
                              check=lambda u: next(answers[u]), sleep=sleeps.append,
                              clock=iter(range(0, 1000, 5)).__next__)
    assert out == []
    assert sleeps == [5, 5]


def test_wait_until_live_gives_up_and_reports():
    out = se._wait_until_live(["a", "b"], timeout_s=10, poll_s=5,
                              check=lambda u: u == "b", sleep=lambda s: None,
                              clock=iter(range(0, 1000, 5)).__next__)
    assert out == ["a"]


def test_distribute_waits_for_live_media_before_loading():
    order = []
    with mock.patch.object(se, "_social_distribution_enabled", return_value=True), \
         mock.patch.object(se, "_wait_until_live", side_effect=lambda urls: order.append(("wait", list(urls))) or []), \
         mock.patch("services.social_load_service.run_social_load",
                    side_effect=lambda jobs, commit: order.append(("load", len(jobs))) or {"ok": True}):
        r = se._distribute_social(CFG, POST, "some-slug", LIVE)
    assert r["ok"] is True
    assert [o[0] for o in order] == ["wait", "load"]
    waited = order[0][1]
    assert LIVE in waited and "https://theaiphoneguy.com/blog/some-slug-hero.png" in waited


def test_distribute_does_not_load_when_site_never_goes_live():
    with mock.patch.object(se, "_social_distribution_enabled", return_value=True), \
         mock.patch.object(se, "_wait_until_live", return_value=["https://theaiphoneguy.com/blog/some-slug-hero.png"]), \
         mock.patch("services.social_load_service.run_social_load") as load:
        r = se._distribute_social(CFG, POST, "some-slug", LIVE)
    load.assert_not_called()
    assert r["ok"] is False and r["error"] == "not live after wait"
