"""TDD for the run_brand format branch + the auto_merge:false safety ceiling.

- format=="ts_posts_array" -> file-map has posts.ts (+images), NO .mdx/.social.md,
  and the MDX assemble path is never called.
- format absent -> the old MDX behavior (regression guard for the other 3 brands).
- config auto_merge:false -> opens the PR and STOPS; never squash-merges, even if
  a caller passes auto_merge=True (config is a hard ceiling).
All network/LLM/GitHub is mocked -- hermetic.
"""
from unittest import mock

from services import slipstream_engine as se

_WD_POST = {
    "title": "How Do I Choose a Marketing Agency?",
    "description": "D", "slug": "wd-post",
    "body": [{"type": "answer", "text": "a"}],
    "image_prompts": [{"name": "hero", "prompt": "h"}, {"name": "gap", "prompt": "g"}],
    "social": {"linkedin": "li", "x": "x"},
}
_WD_CFG = {"brand_key": "worshipdigital", "repo": "salesdroid/worship-digital",
           "domain": "worshipdigital.co", "format": "ts_posts_array",
           "posts_file": "src/content/posts.ts", "business_key": "worshipdigital",
           "auto_merge": False, "money_pages": ["/quote"], "voice": "wd"}


def _patch_wd(violations, ts_contents="NEW_POSTS_TS", publish_url="https://gh/pull/9"):
    return (
        mock.patch.object(se, "_brand_cfg", return_value=_WD_CFG),
        mock.patch.object(se, "generate_post", return_value=_WD_POST),
        mock.patch.object(se, "generate_images", return_value={"hero": b"H", "gap": b"G"}),
        mock.patch.object(se, "assemble_ts_posts", return_value=(ts_contents, violations)),
        mock.patch.object(se, "assemble_mdx",
                          side_effect=AssertionError("MDX assemble must not run for WD")),
        mock.patch.object(se, "publish_post", return_value=publish_url),
    )


def _files_of(pub):
    return pub.call_args.kwargs.get("files") or pub.call_args.args[2]


def test_wd_ts_filemap_writes_posts_ts_and_images_no_mdx():
    ps = _patch_wd([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5] as pub:
        out = se.run_brand("worshipdigital", topic="t", token="tok", date_str="2026-07-25")
    assert out["ok"] is True
    files = _files_of(pub)
    paths = list(files.keys())
    assert "src/content/posts.ts" in paths
    assert files["src/content/posts.ts"] == "NEW_POSTS_TS"
    assert "public/blog/wd-post-hero.png" in paths
    assert "public/blog/wd-post-gap.png" in paths
    # the whole point: no MDX and no committed social.md in the WD repo
    assert not any(p.endswith(".mdx") for p in paths)
    assert not any(p.endswith(".social.md") for p in paths)


def test_wd_auto_merge_false_opens_pr_but_never_merges():
    ps = _patch_wd([])
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], \
         mock.patch.object(se, "merge_when_green",
                           side_effect=AssertionError("must never auto-merge WD")) as mw:
        # even though the CALLER asks for auto_merge=True, config auto_merge:false wins
        out = se.run_brand("worshipdigital", topic="t", token="tok",
                           date_str="2026-07-25", auto_merge=True)
    assert out["ok"] is True
    assert out["pr_url"] == "https://gh/pull/9"
    assert out["published"] is False
    mw.assert_not_called()


def test_wd_gate_violation_holds_and_does_not_publish():
    ps = _patch_wd(["missing required block: quote"])
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5] as pub:
        out = se.run_brand("worshipdigital", topic="t", token="tok", date_str="2026-07-25")
    assert out["ok"] is False
    assert out["held"] is True
    assert any("quote" in v.lower() for v in out["violations"])
    pub.assert_not_called()


def test_format_absent_uses_mdx_path_and_not_ts():
    """Regression guard: the 3 existing brands (no `format`) keep the MDX behavior."""
    post = {"title": "T", "description": "D", "slug": "m",
            "body_mdx": "<AnswerFirst>a</AnswerFirst>",
            "image_prompts": [{"name": "hero", "prompt": "h"}],
            "social": {"linkedin": "l", "x": "x"}}
    cfg = {"brand_key": "autointelligence", "repo": "salesdroid/automotive-intelligence",
           "domain": "automotiveintelligence.io", "blog_dir": "src/content/blog",
           "business_key": "autointelligence"}
    with mock.patch.object(se, "_brand_cfg", return_value=cfg), \
         mock.patch.object(se, "generate_post", return_value=post), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_mdx", return_value=("MDX", [])), \
         mock.patch.object(se, "assemble_ts_posts",
                           side_effect=AssertionError("TS path must not run for an MDX brand")) as ts, \
         mock.patch.object(se, "publish_post", return_value="https://gh/pull/1") as pub:
        out = se.run_brand("autointelligence", topic="t", token="tok",
                           date_str="2026-07-25", auto_merge=False)
    assert out["ok"] is True
    files = _files_of(pub)
    assert "src/content/blog/m.mdx" in files
    assert "src/content/blog/m.social.md" in files
    ts.assert_not_called()
