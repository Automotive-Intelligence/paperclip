"""The AI-detection soft signal: chunking, the verdict, every status, and the
one property that matters most: it can never hold or fail a publish. All
offline; the model is never loaded (the runner is injected)."""
import subprocess
from unittest import mock

import pytest

from services import slipstream_ai_detect as ad
from services import slipstream_engine as se

LONG = " ".join(["Dealers lose leads when nobody answers the phone after six."] * 40)   # 400 words
ON = {"ai_detect": {"enabled": True, "threshold": 0.8, "min_words": 250,
                    "chunk_tokens": 600, "overlap_tokens": 50, "timeout_s": 5}}


def _runner(scores):
    def run(text, chunk_tokens, overlap_tokens, timeout_s):
        return {"ok": True, "scores": list(scores), "texts": [f"chunk {i} text" for i in range(len(scores))]}
    return run


# ---- chunking ---------------------------------------------------------------

def test_short_text_is_one_chunk():
    assert ad.chunk_spans(300, 600, 50) == [(0, 300)]
    assert ad.chunk_spans(0) == []


def test_chunks_cover_everything_overlap_and_stay_inside_the_model_window():
    spans = ad.chunk_spans(2000, 600, 50)
    assert spans[0] == (0, 600) and spans[-1][1] == 2000
    assert all(b - a == 600 for a, b in spans)                 # last window pulled back, never a scrap
    assert all(spans[i + 1][0] < spans[i][1] for i in range(len(spans) - 1))   # no gap
    assert spans[1][0] == 550                                  # 50 tokens of overlap


def test_chunk_size_is_clamped_under_the_768_token_context():
    assert all(b - a <= ad.MODEL_MAX_TOKENS - 2 for a, b in ad.chunk_spans(5000, 5000, 0))


# ---- verdict: Python decides -------------------------------------------------

def test_score_is_the_plain_average_and_flag_is_at_or_above_threshold():
    v = ad.verdict([0.9, 0.5, 1.0], ["a", "b", "c"], 0.8)
    assert v["score"] == 0.8 and v["flagged"] is True
    assert v["flagged_chunks"] == [1, 3]
    assert ad.verdict([0.9, 0.5], ["a", "b"], 0.8)["flagged"] is False
    # one hot chunk is highlighted for the editor even when the post is not flagged
    assert ad.verdict([0.9, 0.5], ["a", "b"], 0.8)["flagged_chunks"] == [1]


def test_no_threshold_means_nothing_can_flag():
    v = ad.verdict([1.0, 1.0], ["a", "b"], None)
    assert v["flagged"] is False and v["flagged_chunks"] == []


# ---- statuses ---------------------------------------------------------------

def test_off_by_default_and_does_no_work():
    boom = mock.Mock(side_effect=AssertionError("must not run"))
    r = ad.check_text(LONG, {}, runner=boom)
    assert r["status"] == "off" and r["ran"] is False and r["flagged"] is False
    assert ad.DEFAULTS["enabled"] is False


def test_short_copy_is_never_scored():
    boom = mock.Mock(side_effect=AssertionError("must not run"))
    r = ad.check_text("A short social post about missed calls. " * 5, ON, runner=boom)
    assert r["status"] == "skipped_short" and r["flagged"] is False


def test_flagged_and_clean():
    r = ad.check_text(LONG, ON, runner=_runner([0.95, 0.9]))
    assert r["status"] == "ok" and r["flagged"] is True and r["flagged_chunks"] == [1, 2]
    assert r["meaning"] == "reads machine-made, needs a human edit pass"
    r = ad.check_text(LONG, ON, runner=_runner([0.1, 0.2]))
    assert r["status"] == "ok" and r["flagged"] is False


def test_uncalibrated_scores_but_never_flags():
    cfg = {"ai_detect": {**ON["ai_detect"], "threshold": None}}
    r = ad.check_text(LONG, cfg, runner=_runner([0.99]))
    assert r["status"] == "uncalibrated" and r["flagged"] is False and r["score"] == 0.99


@pytest.mark.parametrize("exc", [
    ModuleNotFoundError("No module named 'torch'"),
    subprocess.TimeoutExpired("detector", 5),
    RuntimeError("detector process exited -9 with no result"),
    MemoryError(),
])
def test_check_down_is_unavailable_never_an_exception_never_a_flag(exc):
    r = ad.check_text(LONG, ON, runner=mock.Mock(side_effect=exc))
    assert r["status"] == "unavailable" and r["ran"] is False and r["flagged"] is False
    assert r["error"]


def test_empty_scores_is_unavailable_not_a_clean_pass():
    r = ad.check_text(LONG, ON, runner=_runner([]))
    assert r["status"] == "unavailable"


def test_post_text_is_body_only_no_social_no_title():
    post = {"title": "TITLE-ONLY", "body_mdx": "## Heading\n\nBody sentence one. Body sentence two.",
            "social": {"linkedin": "SOCIAL-ONLY post"}, "faq": [{"q": "FAQ-Q?", "a": "FAQ-A."}]}
    t = ad.post_text(post)
    assert "Body sentence one." in t
    assert "SOCIAL-ONLY" not in t and "TITLE-ONLY" not in t and "FAQ-A" not in t


def test_config_merge_defaults_block_and_brand_override():
    full = {"ai_detect": {"enabled": True, "threshold": 0.9, "junk": 1,
                          "brands": {"paperandpurpose": {"enabled": False}}}}
    assert ad.resolve_ai_detect_cfg(full, "aiphoneguy")["threshold"] == 0.9
    assert ad.resolve_ai_detect_cfg(full, "aiphoneguy")["enabled"] is True
    assert ad.resolve_ai_detect_cfg(full, "paperandpurpose")["enabled"] is False
    assert "junk" not in ad.resolve_ai_detect_cfg(full, "aiphoneguy")
    assert ad.resolve_ai_detect_cfg({}, "x")["enabled"] is False


def test_shipped_config_is_off():
    se._load_cfg.cache_clear()
    for brand in (se._load_cfg().get("brands") or {}):
        assert se._brand_cfg(brand)["ai_detect"]["enabled"] is False


# ---- PR section ---------------------------------------------------------------

def test_pr_section_says_what_the_flag_means_and_lists_chunks():
    r = ad.check_text(LONG, ON, runner=_runner([0.95, 0.3]))
    r2 = ad.check_text(LONG, ON, runner=_runner([0.95, 0.9]))
    assert "not flagged" in ad.pr_section(r)
    s = ad.pr_section(r2)
    assert "reads machine-made, needs a human edit pass" in s and "never a hold" in s
    assert "| 1 | 0.95 |" in s and chr(0x2014) not in s
    assert ad.pr_section(None) == "" and ad.pr_section({"status": "off"}) == ""
    down = ad.check_text(LONG, ON, runner=mock.Mock(side_effect=RuntimeError("x")))
    assert "did not run" in ad.pr_section(down)


# ---- the engine: publishing never depends on the check --------------------------

_POST = {"title": "T", "description": "D", "slug": "my-post",
         "body_mdx": "<AnswerFirst>a</AnswerFirst>",
         "image_prompts": [{"name": "hero", "prompt": "h"}],
         "social": {"linkedin": "li", "x": "x"}}
_CFG = {"brand_key": "autointelligence", "repo": "salesdroid/automotive-intelligence",
        "domain": "automotiveintelligence.io", "blog_dir": "src/content/blog",
        "business_key": "autointelligence", "factcheck": {"enabled": False}}


def _run(ai_cfg, detect):
    seen = {}

    def publish(repo, branch, files, title, body, token):
        seen["body"] = body
        return "https://github.com/x/y/pull/1"

    with mock.patch.object(se, "_brand_cfg", return_value={**_CFG, "ai_detect": ai_cfg}), \
         mock.patch.object(se, "generate_post", return_value=dict(_POST)), \
         mock.patch.object(se, "assemble_mdx", return_value=("MDX", [])), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"x"}), \
         mock.patch.object(se, "publish_post", side_effect=publish), \
         mock.patch.object(se, "run_ai_detect", side_effect=detect), \
         mock.patch("services.brief_items.add", return_value=True) as add:
        out = se.run_brand("autointelligence", topic="t", token="tok", auto_merge=False)
    return out, seen, add


def test_engine_flagged_post_still_publishes_and_is_routed_to_a_human():
    flagged = ad.check_text(LONG, ON, runner=_runner([0.95, 0.9]))
    out, seen, add = _run(ON["ai_detect"], lambda post, cfg: flagged)
    assert out["ok"] is True and out["pr_url"] and out["violations"] == []
    assert out["ai_detect"]["flagged"] is True and out["ai_detect_routed"] is True
    assert "AI-detection check" in seen["body"] and "needs a human edit pass" in seen["body"]
    assert add.call_args.args[0] == "edit"
    assert add.call_args.kwargs["url"] == "https://github.com/x/y/pull/1"


def test_engine_clean_post_files_nothing():
    clean = ad.check_text(LONG, ON, runner=_runner([0.1]))
    out, seen, add = _run(ON["ai_detect"], lambda post, cfg: clean)
    assert out["ok"] is True and out["ai_detect"]["flagged"] is False
    add.assert_not_called()


def test_engine_check_crash_never_stops_the_publish():
    def boom(post, cfg):
        raise RuntimeError("detector exploded")
    out, seen, add = _run(ON["ai_detect"], boom)
    assert out["ok"] is True and out["pr_url"]
    assert out["ai_detect"]["status"] == "unavailable"
    add.assert_not_called()


def test_engine_check_off_leaves_receipt_and_pr_body_untouched():
    off = ad.check_text(LONG, {})
    out, seen, add = _run({"enabled": False}, lambda post, cfg: off)
    assert out["ok"] is True and "ai_detect" not in out
    assert "AI-detection" not in seen["body"]
