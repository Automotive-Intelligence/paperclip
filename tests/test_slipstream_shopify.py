"""TDD for the shopify_article format: the P&P (client storefront) publish leg.

The bar that matters most here is NEGATIVE: this path must never publish, never
open a PR, never auto-merge and never fire social. P&P is CLIENT work and is
Miriam-gated, so a regression that flips any of those is a client incident, not a
build break. Everything network is mocked -- hermetic.
"""
import base64
from unittest import mock

import pytest

from services import slipstream_engine as se
from services import slipstream_shopify as sh

_PP_CFG = {
    "brand_key": "paperandpurpose", "domain": "paperandpurpose.co",
    "format": "shopify_article", "business_key": "paperandpurpose",
    "shopify_secret_suffix": "PAPERANDPURPOSE", "blog_handle": "news",
    "author": "Miriam Rubio", "money_pages": ["/pages/the-journal"],
    "queue_repo": "salesdroid/avo-telemetry",
    "queue_path": "scripts/blog_queues/pp_topics.md", "voice": "pp",
}

_PP_POST = {
    "title": "How do I start a prayer journal?",
    "description": "A short way to begin.", "slug": "start-a-prayer-journal",
    "heroAlt": "A cream journal in morning light",
    "faq": [{"q": "How long?", "a": "Five minutes."}],
    "body": [
        {"type": "answer", "text": "Start with five minutes and one prompt."},
        {"type": "definition", "term": "Prayer journal", "text": "A written record."},
        {"type": "callout", "title": "You are not behind", "text": "You are becoming."},
        {"type": "quote", "text": "Ninety days is long enough."},
        {"type": "image", "src": "/blog/start-a-prayer-journal-in-body-1.png", "alt": "a"},
        {"type": "image", "src": "/blog/start-a-prayer-journal-prompts.png", "alt": "b"},
    ],
    "image_prompts": [{"name": "hero", "prompt": "h"}],
    "social": {"linkedin": "li", "x": "x"},
}

_IMAGES = {"hero": b"HERO", "in-body-1": b"1", "prompts": b"2"}


# ---------------------------------------------------------------------------
# the draft-only guarantee
# ---------------------------------------------------------------------------


def test_payload_is_always_unpublished():
    draft, violations = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    assert violations == []
    payload = sh._article_payload(draft)
    assert payload["article"]["published"] is False


def test_payload_ignores_a_caller_supplied_published_flag():
    """A `published` key smuggled in via the draft dict must not reach Shopify."""
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    draft["published"] = True
    assert sh._article_payload(draft)["article"]["published"] is False


def test_hero_rides_as_a_base64_attachment():
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    img = sh._article_payload(draft)["article"]["image"]
    assert base64.b64decode(img["attachment"]) == b"HERO"
    assert img["alt"] == "A cream journal in morning light"


def test_dry_run_never_calls_the_write_api():
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    with mock.patch.object(sh, "resolve_token", return_value="tok"), \
         mock.patch.object(sh, "resolve_blog_id", return_value=42), \
         mock.patch.object(sh, "find_article_by_handle", return_value=None), \
         mock.patch.object(sh, "_shop_host", return_value="s.myshopify.com"), \
         mock.patch.object(sh.requests, "post",
                           side_effect=AssertionError("dry_run must not POST")):
        res = sh.create_article_draft(_PP_CFG, draft, dry_run=True)
    assert res["ok"] and res["dry_run"]
    assert res["would_create"]["article"]["published"] is False


def test_env_kill_switch_forces_dry_run(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_SHOPIFY_DRY_RUN", "1")
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    with mock.patch.object(sh, "resolve_token", return_value="tok"), \
         mock.patch.object(sh, "resolve_blog_id", return_value=42), \
         mock.patch.object(sh, "find_article_by_handle", return_value=None), \
         mock.patch.object(sh, "_shop_host", return_value="s.myshopify.com"), \
         mock.patch.object(sh.requests, "post",
                           side_effect=AssertionError("kill switch must not POST")):
        assert sh.create_article_draft(_PP_CFG, draft, dry_run=False)["dry_run"] is True


def test_duplicate_handle_refuses_a_second_draft():
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    with mock.patch.object(sh, "resolve_token", return_value="tok"), \
         mock.patch.object(sh, "resolve_blog_id", return_value=42), \
         mock.patch.object(sh, "_shop_host", return_value="s.myshopify.com"), \
         mock.patch.object(sh, "find_article_by_handle", return_value={"id": 7}), \
         mock.patch.object(sh.requests, "post",
                           side_effect=AssertionError("duplicate must not POST")):
        res = sh.create_article_draft(_PP_CFG, draft, dry_run=False)
    assert res["ok"] is False and res["duplicate"] is True


def test_a_live_readback_is_forced_back_to_draft():
    """Fail-closed: if Shopify ever returns a published article, put it back."""
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    created = mock.Mock(status_code=201)
    created.json.return_value = {"article": {"id": 9, "handle": "h",
                                             "published_at": "2026-08-03T00:00:00Z"}}
    with mock.patch.object(sh, "resolve_token", return_value="tok"), \
         mock.patch.object(sh, "resolve_blog_id", return_value=42), \
         mock.patch.object(sh, "find_article_by_handle", return_value=None), \
         mock.patch.object(sh, "_shop_host", return_value="s.myshopify.com"), \
         mock.patch.object(sh, "_admin_base", return_value="https://s/admin/api/v"), \
         mock.patch.object(sh.requests, "post", return_value=created), \
         mock.patch.object(sh, "_force_draft", return_value=True) as forced:
        res = sh.create_article_draft(_PP_CFG, draft, dry_run=False)
    assert forced.called
    assert res["draft_verified"] is False and res["forced_back_to_draft"] is True


# ---------------------------------------------------------------------------
# gate + rendering
# ---------------------------------------------------------------------------


def test_gate_holds_an_em_dash():
    bad = {**_PP_POST, "body": _PP_POST["body"] + [{"type": "p", "text": "a — b"}]}
    draft, violations = sh.assemble_shopify_article(bad, "2026-08-03", _PP_CFG, _IMAGES)
    assert draft == {}
    assert any("em-dash" in v for v in violations)


def test_gate_holds_when_the_answer_block_is_not_first():
    bad = {**_PP_POST, "body": list(reversed(_PP_POST["body"]))}
    draft, violations = sh.assemble_shopify_article(bad, "2026-08-03", _PP_CFG, _IMAGES)
    assert draft == {} and violations


def test_body_html_is_escaped():
    evil = {**_PP_POST,
            "body": [{"type": "answer", "text": "<script>alert(1)</script>"}] + _PP_POST["body"][1:]}
    draft, violations = sh.assemble_shopify_article(evil, "2026-08-03", _PP_CFG, _IMAGES)
    assert violations == []
    assert "<script>" not in draft["body_html"]
    assert "&lt;script&gt;" in draft["body_html"]


@pytest.mark.parametrize("src,slug,expected", [
    ("/blog/my-post-hero.png", "my-post", "hero"),
    ("/blog/my-post-in-body-1.png", "my-post", "in-body-1"),  # multi-word name
])
def test_image_name_recovery(src, slug, expected):
    assert sh._image_name(src, slug) == expected


def test_in_body_images_are_omitted_and_reported_without_a_host():
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", _PP_CFG, _IMAGES)
    assert draft["omitted_images"] == ["in-body-1", "prompts"]
    assert "<img" not in draft["body_html"]


def test_in_body_images_render_when_image_host_base_is_set():
    cfg = {**_PP_CFG, "image_host_base": "https://cdn.example.com/"}
    draft, _ = sh.assemble_shopify_article(_PP_POST, "2026-08-03", cfg, _IMAGES)
    assert draft["omitted_images"] == []
    assert 'src="https://cdn.example.com/start-a-prayer-journal-in-body-1.png"' in draft["body_html"]


# ---------------------------------------------------------------------------
# the engine branch
# ---------------------------------------------------------------------------


def _patch_engine(draft_result):
    return (
        mock.patch.object(se, "_brand_cfg", return_value=_PP_CFG),
        mock.patch.object(se, "generate_post", return_value=_PP_POST),
        mock.patch.object(se, "generate_images", return_value=_IMAGES),
        mock.patch.object(se, "assemble_mdx",
                          side_effect=AssertionError("MDX assemble must not run for P&P")),
        mock.patch.object(se, "assemble_ts_posts",
                          side_effect=AssertionError("TS assemble must not run for P&P")),
        mock.patch.object(se, "publish_post",
                          side_effect=AssertionError("P&P has no repo: never open a PR")),
        mock.patch.object(se, "merge_when_green",
                          side_effect=AssertionError("P&P must never auto-merge")),
        mock.patch.object(se, "_distribute_social",
                          side_effect=AssertionError("P&P must never fire social")),
        mock.patch.object(se, "create_article_draft", return_value=draft_result),
        mock.patch.object(se, "_checkoff_topic", return_value=True),
    )


def test_engine_creates_a_draft_and_never_touches_github():
    ok = {"ok": True, "dry_run": False, "article_id": 5, "draft_verified": True,
          "admin_url": "https://admin.shopify.com/store/s/articles/5"}
    ps = _patch_engine(ok)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8] as create, ps[9]:
        res = se.run_brand("paperandpurpose", topic="t", token="gh")
    assert res["ok"] is True
    assert res["published"] is False           # a Shopify DRAFT is the ceiling
    assert res["draft_verified"] is True
    assert create.call_args.kwargs["dry_run"] is False


def test_engine_passes_dry_run_through():
    ps = _patch_engine({"ok": True, "dry_run": True, "store": "s", "blog_id": 1})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8] as create, ps[9]:
        res = se.run_brand("paperandpurpose", topic="t", token="gh", dry_run=True)
    assert res["ok"] and res["dry_run"] is True
    assert create.call_args.kwargs["dry_run"] is True


def test_engine_holds_on_a_gate_violation_before_any_shopify_call():
    with mock.patch.object(se, "_brand_cfg", return_value=_PP_CFG), \
         mock.patch.object(se, "generate_post", return_value=_PP_POST), \
         mock.patch.object(se, "generate_images", return_value=_IMAGES), \
         mock.patch.object(se, "assemble_shopify_article", return_value=({}, ["boom"])), \
         mock.patch.object(se, "create_article_draft",
                           side_effect=AssertionError("must not reach Shopify on a HOLD")):
        res = se.run_brand("paperandpurpose", topic="t", token="gh")
    assert res["held"] is True and res["violations"] == ["boom"]
