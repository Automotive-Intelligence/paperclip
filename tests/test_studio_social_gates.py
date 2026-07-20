"""Gates: deterministic em-dash guard; Iris fails CLOSED; copy gates pass through on error."""
from unittest import mock

from services import studio_social_gates as GT
from services.studio_social_llm import LLMError

CFG = {"display_name": "Automotive Intelligence", "voice": "diagnostic",
       "themes_note": "dealer value", "business_key": "autointelligence"}


def test_sanitize_em_dashes_replaces_and_collapses():
    assert GT.sanitize_em_dashes("fast, cheap — pick two") == "fast, cheap, pick two"
    assert GT.sanitize_em_dashes("a—b") == "a, b"
    assert "—" not in GT.sanitize_em_dashes("one — two — three")


def test_sanitize_posts_hits_every_platform():
    posts = [{"key": "p1", "platforms": {"x": "a — b", "linkedin": "c — d"}}]
    GT.sanitize_posts(posts)
    assert posts[0]["platforms"] == {"x": "a, b", "linkedin": "c, d"}


def test_iris_pass_verdict():
    with mock.patch.object(GT, "llm_json", return_value={"passed": True, "reason": "clean"}):
        v = GT.iris_review(b"PNG", CFG)
    assert v == {"passed": True, "reason": "clean"}


def test_iris_fails_closed_on_llm_error():
    with mock.patch.object(GT, "llm_json", side_effect=LLMError("boom")):
        v = GT.iris_review(b"PNG", CFG)
    assert v["passed"] is False           # an unreviewable image must NOT pass


def test_iris_fail_verdict_passthrough():
    with mock.patch.object(GT, "llm_json",
                           return_value={"passed": False, "reason": "garbled text"}):
        v = GT.iris_review(b"PNG", CFG)
    assert v == {"passed": False, "reason": "garbled text"}


def test_scrutineer_applies_fixes():
    posts = [{"key": "p1", "platforms": {"x": "weak", "linkedin": "weak"}}]
    fixed = {"posts": {"p1": {"x": "strong hook", "linkedin": "strong"}}, "notes": ["tightened"]}
    with mock.patch.object(GT, "llm_json", return_value=fixed):
        out = GT.scrutineer_review(posts, CFG)
    assert out["posts"][0]["platforms"]["x"] == "strong hook"


def test_copy_gate_passes_through_on_error():
    posts = [{"key": "p1", "platforms": {"x": "orig", "linkedin": "orig"}}]
    with mock.patch.object(GT, "llm_json", side_effect=LLMError("down")):
        out = GT.conversion_review(posts, CFG)
    assert out["posts"][0]["platforms"]["x"] == "orig"   # copy preserved, post not dropped
