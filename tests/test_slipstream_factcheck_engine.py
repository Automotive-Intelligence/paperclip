"""How run_brand uses the fact-check gate: order (before images, before any PR),
the single revision, HOLD on failure, HOLD on a gate error, the receipt and the
PR-body audit table. Plus the writer-side pieces (fact rules in the prompt,
revise_post) and the dry-run script's input parsing. All offline."""
import importlib.util
import logging
import os
from unittest import mock

from services import slipstream_engine as se
from services import slipstream_factcheck as fc
from services import slipstream_generate as gen

EM_DASH = chr(0x2014)

_POST = {
    "title": "T", "description": "D", "slug": "my-post",
    "body_mdx": "<AnswerFirst>a</AnswerFirst>",
    "image_prompts": [{"name": "hero", "prompt": "h"}, {"name": "gap", "prompt": "g"}],
    "social": {"linkedin": "li", "x": "x"},
}
_REVISED = dict(_POST, body_mdx="<AnswerFirst>revised</AnswerFirst>", slug="model-changed-the-slug",
                image_prompts=[{"name": "hero", "prompt": "different"}])
_CFG = {"brand_key": "autointelligence", "repo": "salesdroid/automotive-intelligence",
        "domain": "automotiveintelligence.io", "blog_dir": "src/content/blog",
        "business_key": "autointelligence", "money_pages": ["/services"], "voice": "diagnostic"}


def _gate(ok, violations=(), error=None, claims=()):
    return {"ok": ok, "violations": list(violations), "error": error, "claims": list(claims),
            "editor": [], "links": [], "model": "claude-opus-5-5", "llm_ran": True,
            "counts": {"claims": len(claims), "links_checked": 2, "figures_checked": 1,
                       "violations": len(violations)}}


class Harness:
    """Patches every side effect of run_brand and records the ORDER of calls."""

    def __init__(self, gate_results, fmt_violations=None, revise=None, cfg=None):
        self.order = []
        self.gate_results = list(gate_results)
        self.gate_calls = []
        self.fmt_violations = list(fmt_violations or [[]])
        self.revise = revise
        self.cfg = cfg or _CFG

    def _run_factcheck(self, post, cfg, **kw):
        self.order.append("factcheck")
        self.gate_calls.append((post, kw))
        r = self.gate_results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def _assemble(self, post, date_str):
        self.order.append("assemble")
        v = self.fmt_violations.pop(0) if len(self.fmt_violations) > 1 else self.fmt_violations[0]
        return f"MDX:{post['body_mdx']}", v

    def _images(self, prompts, key):
        self.order.append("images")
        return {"hero": b"H", "gap": b"G"}

    def _revise(self, cfg, topic, post, violations):
        self.order.append("revise")
        self.revise_args = (topic, post, violations)
        if isinstance(self.revise, Exception):
            raise self.revise
        return dict(self.revise or _REVISED)

    def _publish(self, repo, branch, files, title, body, token):
        self.order.append("publish")
        self.files, self.pr_body = files, body
        return "https://gh/pull/1"

    def run(self, **kw):
        with mock.patch.object(se, "_brand_cfg", return_value=self.cfg), \
             mock.patch.object(se, "generate_post", return_value=dict(_POST)), \
             mock.patch.object(se, "generate_images", side_effect=self._images), \
             mock.patch.object(se, "assemble_mdx", side_effect=self._assemble), \
             mock.patch.object(se, "revise_post", side_effect=self._revise), \
             mock.patch.object(se, "run_factcheck", side_effect=self._run_factcheck), \
             mock.patch.object(se, "publish_post", side_effect=self._publish):
            return se.run_brand("autointelligence", topic="a topic", token="tok",
                                date_str="2026-11-13", auto_merge=False, **kw)


CLAIM = {"claim": "FTC requires an inventory | of data", "source": "https://ftc.example/rule",
         "verdict": "SUPPORTED", "quote": "q"}


def test_gate_runs_after_the_format_gate_and_before_images_and_the_pr():
    h = Harness([_gate(True, claims=[CLAIM])])
    out = h.run()
    assert h.order == ["assemble", "factcheck", "images", "publish"]
    assert out["ok"] is True and out["pr_url"] == "https://gh/pull/1"
    f = out["factcheck"]
    assert f["ran"] and f["passed"] and f["revised"] is False and f["passes"] == 1
    assert f["model"] == "claude-opus-5-5" and f["counts"]["claims"] == 1


def test_pr_body_carries_the_claim_source_verdict_table():
    h = Harness([_gate(True, claims=[CLAIM])])
    h.run()
    assert "gates: passed (validate_post + factcheck)" in h.pr_body
    assert "| Claim | Source | Verdict |" in h.pr_body
    assert "| FTC requires an inventory \\| of data | https://ftc.example/rule | SUPPORTED |" in h.pr_body
    assert "`claude-opus-5-5`" in h.pr_body and EM_DASH not in h.pr_body


def test_format_gate_failure_never_reaches_the_fact_gate():
    h = Harness([_gate(True)], fmt_violations=[["missing pullquote"]])
    out = h.run()
    assert h.order == ["assemble"] and out["held"] is True
    assert out["violations"] == ["missing pullquote"] and "factcheck" not in out


def test_violations_get_exactly_one_revision_then_the_full_gate_again():
    h = Harness([_gate(False, ["dead source link (HTTP 404): https://x"]), _gate(True)])
    out = h.run()
    assert h.order == ["assemble", "factcheck", "revise", "assemble", "factcheck", "images", "publish"]
    assert h.revise_args[0] == "a topic" and h.revise_args[2] == ["dead source link (HTTP 404): https://x"]
    # the REVISED payload is what gets published, never the rejected one
    assert h.files["src/content/blog/my-post.mdx"] == "MDX:<AnswerFirst>revised</AnswerFirst>"
    f = out["factcheck"]
    assert f["revised"] is True and f["passes"] == 2 and f["passed"] and f["first_pass_violations"] == 1
    assert "revised once by the writer: yes" in h.pr_body
    # second pass reuses the link cache and skips paid calls if code checks fail
    assert h.gate_calls[1][1]["skip_llm_on_deterministic_fail"] is True
    assert h.gate_calls[1][1]["link_cache"] is h.gate_calls[0][1]["link_cache"]
    assert "skip_llm_on_deterministic_fail" not in h.gate_calls[0][1]


def test_revision_cannot_change_the_slug_or_the_image_prompts():
    h = Harness([_gate(False, ["v"]), _gate(True)])
    out = h.run()
    assert out["slug"] == "my-post"
    assert h.gate_calls[1][0]["slug"] == "my-post"
    assert h.gate_calls[1][0]["image_prompts"] == _POST["image_prompts"]
    assert "public/blog/my-post-hero.png" in h.files


def test_still_failing_after_one_revision_holds_with_the_reasons(caplog):
    h = Harness([_gate(False, ["first"]), _gate(False, ['claim is not supported by its source: "s"'])])
    with caplog.at_level(logging.WARNING, logger="services.slipstream_engine"):
        out = h.run()
    assert h.order == ["assemble", "factcheck", "revise", "assemble", "factcheck"]   # no images, no PR
    assert out["ok"] is False and out["held"] is True and out["slug"] == "my-post"
    assert out["violations"] == ['claim is not supported by its source: "s"']
    assert out["factcheck"]["revised"] is True and out["factcheck"]["passed"] is False
    # logged at warning level, in the exact form existing gate holds use
    rec = [r for r in caplog.records if "HELD on gate" in r.getMessage()]
    assert len(rec) == 1 and rec[0].levelno == logging.WARNING
    assert rec[0].getMessage() == ("[slipstream] autointelligence HELD on gate: "
                                   "['claim is not supported by its source: \"s\"']")


def test_never_more_than_one_revision():
    h = Harness([_gate(False, ["a"]), _gate(False, ["b"]), _gate(True)])
    h.run()
    assert h.order.count("revise") == 1 and h.order.count("factcheck") == 2


def test_gate_error_holds_without_paying_for_a_revision():
    h = Harness([_gate(False, ["factcheck gate error (post HELD, never published unchecked): boom"],
                       error="RuntimeError: boom")])
    out = h.run()
    assert h.order == ["assemble", "factcheck"]
    assert out["held"] is True and "gate error" in out["violations"][0]
    assert out["factcheck"]["error"] == "RuntimeError: boom"


def test_gate_that_raises_holds_too():
    h = Harness([RuntimeError("stub exploded")])
    out = h.run()
    assert h.order == ["assemble", "factcheck"] and out["held"] is True and out["ok"] is False
    assert "never published unchecked" in out["violations"][0]


def test_gate_that_raises_on_the_second_pass_holds():
    h = Harness([_gate(False, ["a"]), RuntimeError("down")])
    out = h.run()
    assert out["held"] is True and "publish" not in h.order and "images" not in h.order


def test_failed_revision_holds_with_the_original_reasons():
    h = Harness([_gate(False, ["dead source link"])], revise=gen.GenerationError("LLM 500"))
    out = h.run()
    assert h.order == ["assemble", "factcheck", "revise"]
    assert out["held"] is True and out["violations"][0] == "dead source link"
    assert "revision failed" in out["violations"][1]


def test_revision_that_breaks_the_format_gate_holds():
    h = Harness([_gate(False, ["a"]), _gate(True)], fmt_violations=[[], ["only 1 in-body image(s)"]])
    out = h.run()
    assert h.order == ["assemble", "factcheck", "revise", "assemble"]
    assert out["violations"] == ["revised post failed the format gate", "only 1 in-body image(s)"]


def test_worship_digital_block_format_goes_through_the_same_gate():
    cfg = {**_CFG, "brand_key": "worshipdigital", "format": "ts_posts_array",
           "posts_file": "src/content/posts.ts", "repo": "salesdroid/worship-digital"}
    post = {"title": "T", "description": "D", "slug": "wd-post", "body": [{"type": "answer", "text": "a"}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"x": "x"}}
    seen = []

    def gate(p, c, **kw):
        seen.append(p)
        return _gate(False, ["banned content [first_person_experience]"])
    with mock.patch.object(se, "_brand_cfg", return_value=cfg), \
         mock.patch.object(se, "generate_post", return_value=post), \
         mock.patch.object(se, "assemble_ts_posts", return_value=("TS", [])), \
         mock.patch.object(se, "revise_post", return_value=dict(post)), \
         mock.patch.object(se, "run_factcheck", side_effect=gate), \
         mock.patch.object(se, "generate_images") as gi, \
         mock.patch.object(se, "publish_post") as pub:
        out = se.run_brand("worshipdigital", topic="t", token="tok", date_str="2026-11-13")
    assert out["held"] is True and len(seen) == 2 and isinstance(seen[0]["body"], list)
    gi.assert_not_called()
    pub.assert_not_called()


def test_shopify_brand_is_fact_checked_before_any_image_is_bought():
    cfg = {"brand_key": "paperandpurpose", "format": "shopify_article", "business_key": "paperandpurpose",
           "domain": "paperandpurpose.co"}
    post = {"title": "T", "description": "D", "slug": "pp", "body": [{"type": "answer", "text": "a"}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"x": "x"}}
    with mock.patch.object(se, "_brand_cfg", return_value=cfg), \
         mock.patch.object(se, "generate_post", return_value=post), \
         mock.patch.object(se, "run_factcheck", return_value=_gate(False, ["x"], error="E: down")), \
         mock.patch.object(se, "generate_images") as gi, \
         mock.patch.object(se, "assemble_shopify_article") as asm, \
         mock.patch.object(se, "create_article_draft") as draft:
        out = se.run_brand("paperandpurpose", topic="t", token="tok", date_str="2026-11-13")
    assert out["held"] is True and out["factcheck"]["passed"] is False
    gi.assert_not_called()
    asm.assert_not_called()
    draft.assert_not_called()


def test_shopify_brand_that_passes_still_creates_its_draft():
    cfg = {"brand_key": "paperandpurpose", "format": "shopify_article", "business_key": "paperandpurpose",
           "domain": "paperandpurpose.co"}
    post = {"title": "T", "description": "D", "slug": "pp", "body": [{"type": "answer", "text": "a"}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"x": "x"}}
    with mock.patch.object(se, "_brand_cfg", return_value=cfg), \
         mock.patch.object(se, "generate_post", return_value=post), \
         mock.patch.object(se, "run_factcheck", return_value=_gate(True)), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_shopify_article", return_value=({"title": "T"}, [])), \
         mock.patch.object(se, "create_article_draft", return_value={"ok": True, "dry_run": True}) as draft:
        out = se.run_brand("paperandpurpose", topic="t", token="tok", date_str="2026-11-13", dry_run=True)
    draft.assert_called_once()
    assert out["ok"] is True and out["factcheck"]["passed"] is True


def test_the_real_gate_is_what_run_brand_calls_in_production():
    """conftest stubs se.run_factcheck for offline tests. Prove the name it
    stubs is bound to the real gate, and that there is no bypass switch."""
    import inspect
    src = inspect.getsource(se)
    assert "run_gate as run_factcheck" in src
    assert "SLIPSTREAM_FACTCHECK" not in src and "SLIPSTREAM_SKIP" not in src
    assert inspect.getsource(se._factcheck_stage).count("run_factcheck(") == 2


def test_real_gate_end_to_end_through_run_brand_with_fake_http(monkeypatch):
    """Un-stub the gate: the real run_gate, fed a fake transport and a fake
    model, holds a post with a dead link and a founder anecdote."""
    post = dict(_POST, body_mdx="<AnswerFirst>Michael here. I've sat across the desk from buyers.</AnswerFirst>\n"
                                "See (https://dead.example/page).")
    pages = {"https://dead.example/page": 404, "https://dead.example/": 200}

    class R:
        def __init__(self, code):
            self.status_code, self.text, self.headers = code, "<p>home</p>", {}
    monkeypatch.setattr(fc, "default_http_get", lambda url: R(pages[url]))
    monkeypatch.setattr(fc, "default_llm", lambda model: (lambda s, u: {"claims": []} if s == fc.VERIFIER_SYSTEM
                                                           else {"items": []}))
    monkeypatch.setattr(se, "run_factcheck", fc.run_gate)
    cfg = se._brand_cfg("autointelligence")
    with mock.patch.object(se, "generate_post", return_value=post), \
         mock.patch.object(se, "assemble_mdx", return_value=("MDX", [])), \
         mock.patch.object(se, "generate_images") as gi, \
         mock.patch.object(se, "publish_post") as pub:
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-11-13")
    assert cfg["factcheck"]["banned"]
    assert out["held"] is True
    gi.assert_not_called()
    pub.assert_not_called()


# ------------------------------------------------------------------ writer side

def test_both_writer_prompts_carry_the_fact_rules_and_the_no_facts_notice():
    for cfg in (se._brand_cfg("aiphoneguy"), se._brand_cfg("worshipdigital")):
        prompt = gen._system_prompt_ts(cfg) if cfg.get("format") in gen._BLOCK_FORMATS else gen._system_prompt(cfg)
        assert "FACT RULES" in prompt
        assert "Never write first-person experience" in prompt
        assert "product capability, price, timeline, turnaround or guarantee" in prompt
        assert "stay general or omit the claim" in prompt
        assert "APPROVED BRAND FACTS: none on file" in prompt
        assert EM_DASH not in gen._fact_rules(cfg)
    assert "Call transfer is not live" in gen._system_prompt(se._brand_cfg("aiphoneguy"))
    assert "No religious or faith framing" in gen._system_prompt_ts(se._brand_cfg("worshipdigital"))


def test_writer_prompt_includes_approved_facts_as_its_only_brand_source(tmp_path):
    f = tmp_path / "facts.md"
    f.write_text("<!-- doc -->\n# Approved facts\n- Setup takes 3 business days.\n")
    prompt = gen._system_prompt({"brand_key": "x", "factcheck": {"facts_file": str(f)}})
    assert "your ONLY source for any claim about the brand" in prompt
    assert "Setup takes 3 business days." in prompt and "none on file" not in prompt


def test_revise_post_sends_the_violations_to_the_writer_and_pins_slug_and_images():
    seen = {}

    def llm(system, user):
        seen.update(system=system, user=user)
        return dict(_REVISED, social={"x": "x"})
    with mock.patch.object(gen, "_llm_json", side_effect=llm):
        out = gen.revise_post(_CFG, "the topic", dict(_POST), ["dead source link (HTTP 404): https://x", "second"])
    assert "- dead source link (HTTP 404): https://x" in seen["user"] and "- second" in seen["user"]
    assert "the topic" in seen["user"] and '"slug": "my-post"' in seen["user"]
    assert "Cutting an unsupported claim" in seen["user"] and "Do NOT add any new statistic" in seen["user"]
    assert seen["system"] == gen._system_prompt(_CFG) and EM_DASH not in seen["user"]
    assert out["slug"] == "my-post" and out["image_prompts"] == _POST["image_prompts"]
    assert out["body_mdx"] == "<AnswerFirst>revised</AnswerFirst>"


def test_revise_post_rejects_a_malformed_revision():
    import pytest
    with mock.patch.object(gen, "_llm_json", return_value={"title": "only a title"}):
        with pytest.raises(gen.GenerationError):
            gen.revise_post(_CFG, "t", dict(_POST), ["v"])


# ------------------------------------------------------------------ dry-run script parsing

def _script():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "slipstream_gate_check.py")
    spec = importlib.util.spec_from_file_location("slipstream_gate_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MDX_DIFF = '''diff --git a/src/content/blog/p.mdx b/src/content/blog/p.mdx
new file mode 100644
--- /dev/null
+++ b/src/content/blog/p.mdx
@@ -0,0 +1,6 @@
+---
+title: "How Long: A Guide"
+description: "D"
+---
+
+<AnswerFirst>Live in one to three business days.</AnswerFirst>
diff --git a/src/content/blog/p.social.md b/src/content/blog/p.social.md
--- /dev/null
+++ b/src/content/blog/p.social.md
@@ -0,0 +1,5 @@
+# Social pack: How Long
+
+## LinkedIn
+
+Not six weeks.
'''

TS_DIFF = '''diff --git a/src/content/posts.ts b/src/content/posts.ts
--- a/src/content/posts.ts
+++ b/src/content/posts.ts
@@ -46,6 +46,20 @@ export type Post = {
 export const POSTS: Post[] = [
+  {
+    slug: "gbp",
+    title: "How Do I Optimize: A \\"Plain\\" Guide",
+    faq: [
+      {
+        q: "How often?",
+        a: "Once or twice a week."
+      }
+    ],
+    body: [
+      {
+        type: "p",
+        text: "Michael here. type: not a key"
+      }
+    ]
+  },
   {
     slug: "older",
'''


def test_script_rebuilds_an_mdx_post_and_its_social_pack_from_a_pr_diff(tmp_path):
    p = tmp_path / "a.diff"
    p.write_text(MDX_DIFF)
    post = _script().load_post(str(p))
    assert post["title"] == "How Long: A Guide"
    assert post["body_mdx"].strip() == "<AnswerFirst>Live in one to three business days.</AnswerFirst>"
    assert post["social"] == {"linkedin": "Not six weeks."}


def test_script_rebuilds_a_worship_digital_post_object_from_a_posts_ts_diff(tmp_path):
    p = tmp_path / "wd.diff"
    p.write_text(TS_DIFF)
    post = _script().load_post(str(p))
    assert post["slug"] == "gbp" and post["title"] == 'How Do I Optimize: A "Plain" Guide'
    assert post["body"] == [{"type": "p", "text": "Michael here. type: not a key"}]
    assert post["faq"][0]["a"] == "Once or twice a week."


def test_script_runs_deterministic_only_by_default_and_exits_1_on_hold(tmp_path, monkeypatch, capsys):
    mod = _script()
    p = tmp_path / "a.diff"
    p.write_text(MDX_DIFF)
    seen = {}

    def gate(post, cfg, **kw):
        seen.update(kw)
        return {"ok": False, "violations": ["figure x"], "error": None, "claims": [], "editor": [],
                "links": [], "counts": {"violations": 1}, "model": "m"}
    monkeypatch.setattr(fc, "run_gate", gate)
    assert mod.main([str(p), "--brand", "aiphoneguy"]) == 1
    assert seen == {"use_llm": False}
    out = capsys.readouterr().out
    assert "VERDICT HOLD" in out and "1. figure x" in out
    assert mod.main([str(tmp_path / "missing.diff"), "--brand", "aiphoneguy"]) == 2
