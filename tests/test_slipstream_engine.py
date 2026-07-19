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
         mock.patch.object(se, "_distribute_social", return_value={"ok": True}) as ds:
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-07-19")
    assert out["published"] is True
    assert out["live_url"] == "https://automotiveintelligence.io/blog/my-post"
    mw.assert_called_once()
    ds.assert_called_once()


def test_red_build_holds_pr_no_social():
    ps = _patch_all([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], \
         mock.patch.object(se, "merge_when_green", return_value={"merged": False, "reason": "vercel build failure"}), \
         mock.patch.object(se, "_distribute_social") as ds:
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-07-19")
    assert out["published"] is False
    assert "held" in out["note"].lower()
    ds.assert_not_called()
