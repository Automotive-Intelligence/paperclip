"""--from-receipt: repair a saved, revised post without research, a writer
call or a revision. Offline: fake HTTP, fake models, a temp receipt file."""
import importlib.util
import json
import os
from unittest import mock

import pytest

from services import slipstream_engine as se
from services import slipstream_factcheck as fc
from services import slipstream_generate as gen
from services import slipstream_repair as rp

EM_DASH = chr(0x2014)
SRC = "https://trade.example/report"
SRC2 = "https://gone.example/page"
QUOTE = "Dealers must keep a written inventory of the customer data they hold."
SRC_TEXT = QUOTE + " The survey covered 1,200 stores across the country. " + "Background on the rule follows. " * 30
GOOD = f"Dealers must keep [a written inventory]({SRC}) of the customer data they hold."
OVERREACH = f"Every dealer in the country [has already been fined]({SRC}) for missing one."
FILLER = "The truth is that this matters more than you think."
MDX = "\n".join([f"<AnswerFirst>{GOOD}</AnswerFirst>", "", "## What does the rule ask for?", "",
                 f"{GOOD} {OVERREACH} Start with the systems you already own.", "",
                 "## Where should a dealer start?", "",
                 f"{FILLER} List each system and who can open it. See [services](/services)."])
POST = {"title": "What must a dealer inventory?", "description": "A plain answer for dealers.",
        "slug": "inventory", "body_mdx": MDX, "image_prompts": [{"name": "hero", "prompt": "h"}],
        "social": {"linkedin": "Read the post."}}
CFG = {"brand_key": "autointelligence", "domain": "automotiveintelligence.io", "business_key": "autointelligence",
       "repo": "o/r", "blog_dir": "src/content/blog", "money_pages": ["/services"],
       "factcheck": {"banned": [], "figure_allowlist": [], "repair_max_fail_ratio": 1.0, "repair_min_words": 0}}
PACK = {"mode": "sourced", "min_sources": 2, "facts": "", "internal_links": ["/services", "/old-page"],
        "sources": [{"url": SRC, "publisher": "Trade", "primary": False,
                     "quotes": [{"text": QUOTE, "figures": []},
                                {"text": "A sentence the page has since removed entirely.", "figures": []}]},
                    {"url": SRC2, "publisher": "Gone", "primary": False,
                     "quotes": [{"text": "This page used to exist and say this.", "figures": []}]}],
        "brand_pages": [{"path": "/services", "url": "https://automotiveintelligence.io/services",
                         "quotes": [{"text": "We run a fixed-scope diagnostic of lead handling.", "figures": []}]}]}


def _script():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "slipstream_gate_check.py")
    spec = importlib.util.spec_from_file_location("slipstream_gate_check_receipt", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _script()


class R:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.headers = status, text, {"content-type": "text/html"}


def http(url):
    return {SRC: R(200, f"<p>{SRC_TEXT}</p>"),
            "https://automotiveintelligence.io/services":
                R(200, "<p>We run a fixed-scope diagnostic of lead handling. " + "services " * 100 + "</p>"),
            "https://gone.example/": R(200, "root"),
            }.get(url) or R(404, "nope")


def _uid(startswith):
    units, _ = fc.extract_post(POST)
    return next(u.uid for u in units if u.text.startswith(startswith))


def saved_claims():
    ok = {"claim": "inventory", "source": SRC, "verdict": "SUPPORTED", "quote": QUOTE}
    units, _ = fc.extract_post(POST)
    good = [u.uid for u in units if u.text.startswith("Dealers must keep")]
    return [dict(ok, sentence_id=i) for i in good] + [
        {"sentence_id": _uid("Every dealer"), "claim": "all fined", "source": SRC, "verdict": "NOT_SUPPORTED", "quote": ""}]


VIOLATIONS = ['claim is not supported by its source: "Every dealer in the country has already been fined for missing one." '
              f"[source: {SRC}]",
              'editor: no_filler_or_hype FAIL (There is no filler or hype) evidence: "The truth is that this matters more than you think."']


def write_receipt(tmp_path, **over):
    d = {"brand": "autointelligence", "topic": "the topic", "pack": PACK,
         "posts": [{"stage": "draft", "post": dict(POST, body_mdx="draft")}, {"stage": "revision", "post": POST}],
         "receipt": {"ok": False, "violations": VIOLATIONS,
                     "factcheck": {"revised": True, "passes": 2, "claims": saved_claims()}}}
    d.update(over)
    p = tmp_path / "receipt.json"
    p.write_text(json.dumps(d))
    return str(p)


# ---------------------------------------------------------------- loader

def test_loader_takes_the_post_after_the_revision_with_its_pack_and_verdicts(tmp_path):
    s = G.load_receipt(write_receipt(tmp_path))
    assert s["brand"] == "autointelligence" and s["topic"] == "the topic" and s["stage"] == "revision"
    assert s["post"]["body_mdx"] == MDX and s["pack"]["sources"][0]["url"] == SRC
    assert len(s["claims"]) == 3 and s["violations"] == VIOLATIONS


def test_loader_refuses_a_receipt_it_cannot_trust(tmp_path):
    for over, needle in [({"posts": []}, "no post"), ({"pack": None}, "no brand or no source pack"),
                         ({"brand": ""}, "no brand"),
                         ({"posts": [{"stage": "revision", "post": POST}, {"stage": "repair", "post": POST}]}, "already a repaired"),
                         ({"receipt": {"violations": [], "factcheck": {"revised": False, "claims": []}}}, "gate result is for the draft")]:
        with pytest.raises(ValueError, match=needle):
            G.load_receipt(write_receipt(tmp_path, **over))


# ---------------------------------------------------------------- pack re-check

def test_pack_recheck_drops_what_no_longer_loads_or_no_longer_holds_its_quote():
    cache = {}
    pack, dropped = G.recheck_pack(PACK, CFG, http, cache)
    assert [s["url"] for s in pack["sources"]] == [SRC]
    assert [q["text"] for q in pack["sources"][0]["quotes"]] == [QUOTE]
    assert pack["internal_links"] == ["/services"] and pack["usable_sources"] == 1
    assert [b["path"] for b in pack["brand_pages"]] == ["/services"]
    joined = "\n".join(dropped)
    assert f"source no longer usable (dead link (HTTP 404)): {SRC2}" in joined
    assert 'quote is no longer on the page' in joined and "has since removed entirely" in joined
    assert "internal link no longer returns 200: /old-page" in joined
    assert PACK["sources"][0]["quotes"][1]["text"].startswith("A sentence"), "the saved pack is not mutated"
    assert cache[SRC].state == "live", "the gate reuses what the re-check fetched"


def test_a_brand_page_that_lost_its_quote_is_dropped():
    def http2(url):
        return R(200, "<p>" + "new copy " * 100 + "</p>") if url.endswith("/services") else http(url)
    pack, dropped = G.recheck_pack(PACK, CFG, http2, {})
    assert pack["brand_pages"] == [] and any("none of its quotes is still on the page" in d for d in dropped)


# ---------------------------------------------------------------- saved verdicts

def test_saved_claim_verdicts_become_failures_tied_to_their_sentence():
    fails, aligned = G.saved_claim_failures(POST, saved_claims(), VIOLATIONS)
    assert aligned is True and len(fails) == 1
    f = fails[0]
    assert f["uid"] == _uid("Every dealer") and f["rule"] == "claim" and f["violation"] == VIOLATIONS[0]
    assert f["text"].startswith("Every dealer in the country")


def test_saved_verdicts_that_do_not_line_up_with_the_post_are_not_trusted():
    shifted = [dict(c, sentence_id="S2") if c["verdict"] != "SUPPORTED" else c for c in saved_claims()]
    assert G.saved_claim_failures(POST, shifted, VIOLATIONS) == ([], False)
    gone = saved_claims() + [{"sentence_id": "S999", "verdict": "SUPPORTED", "source": SRC, "quote": QUOTE}]
    assert G.saved_claim_failures(POST, gone, VIOLATIONS) == ([], False)
    assert G.saved_claim_failures(POST, saved_claims(), ["some other violation"]) == ([], False)


# ---------------------------------------------------------------- budget

def test_budget_refuses_a_call_that_would_cross_the_cap_and_never_makes_it():
    gen.USAGE_LOG.clear()
    call = mock.Mock(return_value={"ok": 1})
    tight = G.Budget(cap=0.10, spent_before=0.05)
    with pytest.raises(G.BudgetStop, match="over the \\$0.10 cap"):
        tight.guard("verifier", "claude-opus-5-5", call)("s" * 1000, "u" * 100000)
    call.assert_not_called()
    assert tight.log == [{"kind": "verifier", "estimate": pytest.approx(0.235, abs=0.01), "allowed": False}]
    roomy = G.Budget(cap=1.0)
    assert roomy.guard("editor", "claude-opus-5-5", call)("s", "u") == {"ok": 1}
    assert roomy.log[0]["allowed"] is True and roomy.total() == 0.0
    assert roomy.estimate("repair", "claude-sonnet-5", "", "") == pytest.approx(0.05)


# ---------------------------------------------------------------- end to end

def editor_reply(*failing_ids):
    items = []
    for rid, _q, needs in fc.EDITOR_RUBRIC:
        if rid == "no_filler_or_hype" and failing_ids:
            items.append({"id": rid, "pass": False, "evidence": FILLER, "sentence_ids": list(failing_ids)})
        else:
            items.append({"id": rid, "pass": True, "evidence": QUOTE if needs else "", "sentence_ids": []})
    return {"items": items}


def run(tmp_path, monkeypatch, capsys, final_claims=(), cap=1.0, final_editor_fail=False):
    calls = {"verifier": [], "editor": 0, "repair": 0}

    def gate_llm(system, user):
        if system == fc.EDITOR_SYSTEM:
            calls["editor"] += 1
            bad = [ln.split(" ", 1)[0] for ln in user.splitlines() if "The truth is that" in ln]
            if final_editor_fail and calls["editor"] > 1:
                bad = [ln.split(" ", 1)[0] for ln in user.splitlines() if "List each system" in ln]
            return editor_reply(*bad)
        calls["verifier"].append(user)
        return {"claims": list(final_claims)}

    def repair_llm(system, user):
        calls["repair"] += 1
        out = []
        for chunk in user.split("=== FIELD ")[1:]:
            raw = chunk.split("RAW TEXT:\n", 1)[1].split("\nFAILING SENTENCE", 1)[0]
            out.append({"id": chunk.split(" ", 1)[0],
                        "text": raw.replace(" " + OVERREACH, "").replace(FILLER + " ", "")})
        return {"fields": out}

    never = mock.Mock(side_effect=AssertionError("--from-receipt must not research, write, revise or publish"))
    monkeypatch.setattr(se, "_brand_cfg", lambda b: CFG)
    monkeypatch.setattr(fc, "default_http_get", http)
    monkeypatch.setattr(fc, "default_llm", lambda model: gate_llm)
    monkeypatch.setattr(rp, "default_llm", lambda: repair_llm)
    monkeypatch.setattr(se, "run_factcheck", fc.run_gate)
    monkeypatch.setattr(se, "repair_post", rp.repair_post)
    monkeypatch.setattr(se, "assemble_mdx", lambda p, d: ("ASSEMBLED:" + p["body_mdx"], []))
    for name in ("build_source_pack", "generate_post", "revise_post", "generate_images", "publish_post",
                 "create_article_draft", "_checkoff_topic", "merge_when_green", "_distribute_social", "_next_topic"):
        monkeypatch.setattr(se, name, never)
    out_dir = tmp_path / "out"
    code = G.main(["--from-receipt", write_receipt(tmp_path), "--out", str(out_dir), "--cap", str(cap)])
    never.assert_not_called()
    return code, capsys.readouterr().out, out_dir, calls


def test_from_receipt_repairs_the_saved_post_and_writes_the_final_files_on_a_pass(tmp_path, monkeypatch, capsys):
    code, out, out_dir, calls = run(tmp_path, monkeypatch, capsys)
    assert code == 0 and "VERDICT PASS" in out
    assert "PACK RECHECK 1 source(s) still usable, 1 first-party page(s), 3 thing(s) dropped" in out
    assert "BEFORE REPAIR (2 violations)" in out and "REPAIR  ran=True applied=True targets=2 final_gate_passed=True" in out
    assert "DELETED [body] rules=claim\n    - Every dealer in the country" in out
    assert "DELETED [body] rules=editor:no_filler_or_hype\n    - The truth is" in out
    assert "FINAL VIOLATIONS (0)" in out and "WORDS   body" in out
    assert calls["editor"] == 2 and calls["repair"] == 1 and len(calls["verifier"]) == 1
    assert "Dealers must keep a written inventory of the customer data they hold. LINKS" not in calls["verifier"][0], \
        "sentences verified in the saved pass are carried, not judged again"
    saved = json.loads((out_dir / "autointelligence.json").read_text())
    assert saved["passed"] is True and saved["words_after"] < saved["words_before"]
    assert "fined" not in saved["post_after"]["body_mdx"] and "fined" in saved["post_before"]["body_mdx"]
    final = json.loads((out_dir / "autointelligence-final.json").read_text())
    assert final["published"] is False and final["post"]["slug"] == "inventory"
    assert final["assembled_payload"] == "ASSEMBLED:" + final["post"]["body_mdx"]
    md = (out_dir / "autointelligence-FINAL.md").read_text()
    assert md.startswith("# What must a dealer inventory?\n\n_A plain answer for dealers._")
    assert "> **Answer:** Dealers must keep [a written inventory]" in md and "NOT PUBLISHED" in md
    assert f"- {SRC}" in md and "## What the repair pass changed" in md
    assert "- DELETED (body; rule: claim)\n  - before: Every dealer in the country" in md
    assert "<AnswerFirst>" not in md and EM_DASH not in md


def test_from_receipt_reports_a_hold_and_writes_no_final_file(tmp_path, monkeypatch, capsys):
    code, out, out_dir, calls = run(tmp_path, monkeypatch, capsys, final_editor_fail=True)
    assert code == 1 and "VERDICT HOLD" in out and "final_gate_passed=False" in out
    assert "FINAL VIOLATIONS (1)" in out and "editor: no_filler_or_hype FAIL" in out
    assert not (out_dir / "autointelligence-FINAL.md").exists()
    assert json.loads((out_dir / "autointelligence.json").read_text())["passed"] is False


def test_from_receipt_stops_before_a_paid_call_that_would_cross_the_cap(tmp_path, monkeypatch, capsys):
    code, out, out_dir, calls = run(tmp_path, monkeypatch, capsys, cap=0.001)
    assert code == 1 and "VERDICT STOPPED (spend cap)" in out and "REFUSED" in out
    assert calls == {"verifier": [], "editor": 0, "repair": 0}, "no model was called"
    assert not (out_dir / "autointelligence-FINAL.md").exists()


def test_readable_markdown_renders_a_block_post():
    post = {"title": "T", "description": "D", "slug": "s",
            "body": [{"type": "answer", "text": "Short answer."}, {"type": "h2", "text": "Why?"},
                     {"type": "p", "text": "Because."}, {"type": "ul", "items": ["one", "two"]},
                     {"type": "stat", "value": "2 hours", "label": "limit", "source": "Google", "href": SRC},
                     {"type": "callout", "title": "Rule", "text": "Keep it tight."},
                     {"type": "table", "headers": ["a", "b"], "rows": [["1", "2"]]},
                     {"type": "links", "title": "Next", "items": [{"label": "Quote", "href": "/quote"}]},
                     {"type": "sources", "title": "Sources", "items": [{"label": "Google", "href": SRC}]}],
            "faq": [{"q": "Q?", "a": "A."}], "social": {"x": "post"}}
    md = G.readable_markdown(post, {"brand_key": "wd", "domain": "worshipdigital.co"}, {"changes": []})
    for part in ("# T", "> **Answer:** Short answer.", "## Why?", "- one", f"**2 hours** limit ([Google]({SRC}))",
                 "> **Rule:** Keep it tight.", "| a | b |", "- [Quote](/quote)", "## FAQ", "**Q?**",
                 f"- {SRC}", "**x:** post", "- nothing"):
        assert part in md, part
