"""Round 5: the repair limit counts claims and style lines separately, the
verifier's "no claim here" and the editor's line verdicts are carried between
passes, the pitch sentence has a written rule, and a held post can be kept as a
draft pull request for a human editor. All offline."""
from unittest import mock

import pytest

from services import slipstream_engine as se
from services import slipstream_factcheck as fc
from services import slipstream_github as gh
from services import slipstream_repair as rp

EM_DASH = chr(0x2014)
SRC = "https://trade.example/report"
QUOTE = "Dealers must keep a written inventory of the customer data they hold."
SRC_TEXT = QUOTE + " " + "Background on the rule follows. " * 30
GOOD = f"Dealers must keep [a written inventory]({SRC}) of the customer data they hold."
BAD = f"Every dealer [has already been fined]({SRC}) for missing one."
STYLE = ["The truth is that this matters.", "It is a real game changer.", "Honestly, nobody tells you this.",
         "Here is the thing about lists.", "Let that sink in for a moment."]
PITCH = "Book [a diagnostic call](/services) to talk it through."
MDX = "\n".join([f"<AnswerFirst>{GOOD}</AnswerFirst>", "", "## What does the rule ask for?", "",
                 f"{GOOD} {BAD} Start with the systems you already own.", "",
                 "## Where should a dealer start?", "",
                 " ".join(STYLE) + f" List each system and who can open it. {PITCH}"])
POST = {"title": "What must a dealer inventory?", "description": "A plain answer for dealers.",
        "slug": "inventory", "body_mdx": MDX, "image_prompts": [{"name": "hero", "prompt": "h"}],
        "social": {"linkedin": "Read the post."}}
CFG = {"brand_key": "autointelligence", "domain": "automotiveintelligence.io", "business_key": "autointelligence",
       "repo": "o/r", "blog_dir": "src/content/blog", "money_pages": ["/services"], "auto_merge": True,
       "factcheck": {"banned": [], "figure_allowlist": [], "repair_pass": False}}
PACK = {"sources": [{"url": SRC, "publisher": "Trade", "quotes": [{"text": QUOTE, "figures": []}]}],
        "brand_pages": [], "internal_links": ["/services"], "facts": ""}


class R:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.headers = status, text, {"content-type": "text/html"}


def http(url):
    return {SRC: R(200, f"<p>{SRC_TEXT}</p>"),
            "https://automotiveintelligence.io/services": R(200, "<p>" + "services " * 100 + "</p>")}.get(url) or R(404)


def unit(post, startswith):
    return next(u for c in rp.containers(post) for u in c.units if u.text.startswith(startswith))


def fail(post, startswith, rule):
    u = unit(post, startswith)
    return {"uid": u.uid, "where": u.where, "surface": u.surface, "text": u.text, "rule": rule, "violation": f"v {rule}"}


# ---------------------------------------------------------------------------
# 1. two counts, two limits
# ---------------------------------------------------------------------------

def test_style_lines_do_not_count_against_the_claim_limit():
    """The 2026-11-19 shape: few failing claims, many editor-named style lines."""
    conts = rp.containers(POST)
    fails = [fail(POST, "Every dealer", "claim")] + [fail(POST, s[:12], "editor:no_filler_or_hype") for s in STYLE]
    targets, skip = rp.plan(fails, conts, claims_judged=10, max_fail_ratio=1 / 3, max_style_ratio=0.75)
    assert skip is None and len(targets) == 6, "1 of 10 claims failing; 5 style lines are under their own limit"
    # before this change the same input was 6 of 10 = 60 percent and was refused


def test_too_many_failing_claims_still_skips_whatever_the_style_count():
    conts = rp.containers(POST)
    fails = [fail(POST, "Every dealer", "claim"), fail(POST, "Start with", "figure"),
             fail(POST, "List each system", "pack"), fail(POST, "The truth is", "banned")]
    targets, skip = rp.plan(fails, conts, claims_judged=10, max_fail_ratio=1 / 3, max_style_ratio=0.75)
    assert targets == {} and skip.startswith("4 sentences with a failing claim against 10 judged claims is more "
                                             "than the repair limit (33%)")


def test_style_lines_have_their_own_larger_limit():
    conts = rp.containers(POST)
    fails = [fail(POST, s[:12], "editor:operator_voice") for s in STYLE]
    ok, skip = rp.plan(fails, conts, claims_judged=10, max_fail_ratio=1 / 3, max_style_ratio=0.75)
    assert skip is None and len(ok) == 5
    none, skip = rp.plan(fails, conts, claims_judged=6, max_fail_ratio=1 / 3, max_style_ratio=0.75)
    assert none == {} and skip == ("5 sentences failing only an editor style rule against 6 judged claims is more "
                                   "than the style repair limit (75%); held, not patched")


def test_a_sentence_with_a_claim_and_a_style_fail_counts_once_as_a_claim():
    conts = rp.containers(POST)
    fails = [fail(POST, "Every dealer", "claim"), fail(POST, "Every dealer", "editor:no_filler_or_hype")]
    targets, skip = rp.plan(fails, conts, claims_judged=3, max_fail_ratio=1 / 3, max_style_ratio=0.0)
    assert skip is None and len(targets) == 1


def test_style_limit_is_configurable_and_on_in_the_real_config():
    se._load_cfg.cache_clear()
    f = se._brand_cfg("worshipdigital")["factcheck"]
    assert f["repair_max_style_ratio"] == 0.75 and f["repair_max_style_ratio"] > f["repair_max_fail_ratio"]
    full = {"factcheck": {"defaults": {"repair_max_style_ratio": 0.5}, "brands": {"x": {"repair_max_style_ratio": 2}}}}
    assert fc.resolve_factcheck_cfg(full, "x")["repair_max_style_ratio"] == 2
    assert fc.resolve_factcheck_cfg(full, "y")["repair_max_style_ratio"] == 0.5
    assert fc.resolve_factcheck_cfg({}, "y")["repair_max_style_ratio"] == 0.75


# ---------------------------------------------------------------------------
# gate helpers
# ---------------------------------------------------------------------------

def editor(**fails):
    items = []
    for rid, _q, needs in fc.EDITOR_RUBRIC:
        if rid in fails:
            items.append({"id": rid, "pass": False, "evidence": fails[rid][0], "sentence_ids": list(fails[rid][1])})
        else:
            items.append({"id": rid, "pass": True, "evidence": QUOTE if needs else "", "sentence_ids": []})
    return {"items": items}


def gate(post, claims=(), ed=None, vcache=None, ecache=None, seen=None, cfg=CFG):
    def llm(system, user):
        if seen is not None:
            seen.append((system, user))
        return {"claims": list(claims)} if system == fc.VERIFIER_SYSTEM else (ed or editor())
    return fc.run_gate(post, cfg, http_get=http, llm=llm, facts_text="", verdict_cache=vcache, editor_cache=ecache)


CLEAN = dict(POST, body_mdx=f"<AnswerFirst>{GOOD}</AnswerFirst>\n\nStart with the systems you already own. {PITCH}")


def sup(post):
    return {"sentence_id": unit(post, "Dealers must keep").uid, "claim": "inventory", "kind": "external",
            "source": SRC, "verdict": "SUPPORTED", "quote": QUOTE}


# ---------------------------------------------------------------------------
# 2. "no checkable claim" is carried
# ---------------------------------------------------------------------------

def test_a_sentence_shown_with_no_claim_listed_is_not_shown_again_while_unchanged():
    vc, seen = {}, []
    gate(CLEAN, [sup(CLEAN)], vcache=vc)
    assert vc[("Start with the systems you already own.", (), "body")] == fc.NO_CLAIM
    # second pass: the verifier would now call that sentence an unverifiable claim. It cannot.
    flip = [{"sentence_id": unit(CLEAN, "Start with").uid, "claim": "start there", "kind": "external",
             "source": None, "verdict": "UNVERIFIABLE", "quote": ""}]
    r = gate(CLEAN, flip, vcache=vc, seen=seen)
    assert r["ok"] is True and [s for s, _ in seen] == [fc.EDITOR_SYSTEM], "nothing left to judge: no verifier call"
    assert not [f for f in r["failures"] if f["rule"] == "claim"]


def test_a_changed_sentence_is_judged_fresh_and_can_fail():
    vc, seen = {}, []
    gate(CLEAN, [sup(CLEAN)], vcache=vc)
    changed = dict(CLEAN, body_mdx=CLEAN["body_mdx"].replace("Start with the systems you already own.",
                                                               "Most dealers forget half of their systems."))
    claim = [{"sentence_id": unit(changed, "Most dealers").uid, "claim": "most forget", "kind": "external",
              "source": None, "verdict": "UNVERIFIABLE", "quote": ""}]
    r = gate(changed, claim, vcache=vc, seen=seen)
    assert "Most dealers forget half of their systems." in seen[0][1]
    assert "Dealers must keep a written inventory of the customer data they hold. LINKS" not in seen[0][1]
    assert r["ok"] is False and any(v.startswith("unverifiable claim") for v in r["violations"])


def test_a_new_link_on_a_no_claim_sentence_is_a_new_key():
    vc, seen = {}, []
    gate(CLEAN, [sup(CLEAN)], vcache=vc)
    linked = dict(CLEAN, body_mdx=CLEAN["body_mdx"].replace("Start with the systems you already own.",
                                                             f"Start with [the systems]({SRC}) you already own."))
    gate(linked, [], vcache=vc, seen=seen)
    assert seen[0][0] == fc.VERIFIER_SYSTEM and f"Start with the systems you already own. LINKS: {SRC}" in seen[0][1]


def test_a_failed_sentence_is_never_remembered_as_no_claim():
    vc = {}
    bad = [sup(CLEAN), {"sentence_id": unit(CLEAN, "Start with").uid, "claim": "c", "kind": "external",
                        "source": None, "verdict": "UNVERIFIABLE", "quote": ""}]
    gate(CLEAN, bad, vcache=vc)
    assert ("Start with the systems you already own.", (), "body") not in vc
    seen = []
    gate(CLEAN, [], vcache=vc, seen=seen)
    assert "Start with the systems you already own." in seen[0][1], "still shown: it has no settled verdict"


def test_without_a_cache_nothing_is_carried():
    seen = []
    gate(CLEAN, [sup(CLEAN)], seen=seen)
    gate(CLEAN, [sup(CLEAN)], seen=seen)
    assert [s for s, _ in seen].count(fc.VERIFIER_SYSTEM) == 2


# ---------------------------------------------------------------------------
# 3. the editor is stable on unchanged lines
# ---------------------------------------------------------------------------

def test_an_unchanged_line_judged_clean_cannot_be_newly_failed():
    ec = {}
    r1 = gate(CLEAN, [sup(CLEAN)], editor(), ecache=ec)
    assert r1["ok"] is True and ec[("Start with the systems you already own.", (), "body")] == set()
    start = unit(CLEAN, "Start with")
    harsher = editor(operator_voice=(start.text, [start.uid]))
    r2 = gate(CLEAN, [sup(CLEAN)], harsher, ecache=ec)
    assert r2["ok"] is True, r2["violations"]
    rec = {e["id"]: e for e in r2["editor"]}["operator_voice"]
    assert rec["pass"] is True and rec["carried_clean"] is True
    assert gate(CLEAN, [sup(CLEAN)], harsher)["ok"] is False, "without the cache the same reply is a fail"


def test_an_unchanged_line_that_failed_still_fails_when_the_editor_stops_naming_it():
    post = dict(CLEAN, body_mdx=CLEAN["body_mdx"] + "\n\n" + STYLE[0])
    ec = {}
    bad = unit(post, "The truth is")
    r1 = gate(post, [sup(post)], editor(no_filler_or_hype=(bad.text, [bad.uid])), ecache=ec)
    assert r1["ok"] is False and ec[(bad.text, (), "body")] == {"no_filler_or_hype"}
    r2 = gate(post, [sup(post)], editor(), ecache=ec)                       # the editor now says all clear
    assert r2["ok"] is False
    assert [v for v in r2["violations"] if v.startswith("editor: no_filler_or_hype FAIL")]
    (f,) = [x for x in r2["failures"] if x["rule"] == "editor:no_filler_or_hype"]
    assert f["uid"] == bad.uid and f["carried"] is True


def test_a_rewritten_line_is_judged_fresh_by_the_editor():
    post = dict(CLEAN, body_mdx=CLEAN["body_mdx"] + "\n\n" + STYLE[0])
    ec = {}
    bad = unit(post, "The truth is")
    gate(post, [sup(post)], editor(no_filler_or_hype=(bad.text, [bad.uid])), ecache=ec)
    fixed = dict(post, body_mdx=post["body_mdx"].replace(STYLE[0], "Check the list each quarter."))
    assert gate(fixed, [sup(fixed)], editor(), ecache=ec)["ok"] is True, "the old fail does not follow a changed line"
    new = unit(fixed, "Check the list")
    r = gate(fixed, [sup(fixed)], editor(operator_voice=(new.text, [new.uid])), ecache={k: v for k, v in ec.items()
                                                                                         if k[0] != new.text})
    assert r["ok"] is False, "and a changed line can be failed"


def test_a_fail_that_names_some_clean_and_some_new_lines_keeps_only_the_new_ones():
    ec = {}
    gate(CLEAN, [sup(CLEAN)], editor(), ecache=ec)
    post = dict(CLEAN, body_mdx=CLEAN["body_mdx"] + "\n\n" + STYLE[1])
    old, new = unit(post, "Start with"), unit(post, "It is a real")
    r = gate(post, [sup(post)], editor(no_filler_or_hype=(old.text, [old.uid, new.uid])), ecache=ec)
    named = [f["uid"] for f in r["failures"] if f["rule"] == "editor:no_filler_or_hype"]
    assert named == [new.uid]
    (v,) = [x for x in r["violations"] if x.startswith("editor: no_filler_or_hype")]
    assert "It is a real game changer." in v and "Start with the systems" not in v


def test_whole_post_editor_items_are_judged_fresh_every_pass():
    ec = {}
    gate(CLEAN, [sup(CLEAN)], editor(), ecache=ec)
    r = gate(CLEAN, [sup(CLEAN)], editor(concrete_specific=("", [])), ecache=ec)
    assert r["ok"] is False and any(v.startswith("editor: concrete_specific FAIL") for v in r["violations"])


def test_the_pitch_sentence_has_a_written_rule_and_the_other_items_defer_to_it():
    rubric = {rid: q for rid, q, _n in fc.EDITOR_RUBRIC}
    pitch = rubric["pitch_within_rules"]
    for phrase in ("may name the service and invite", "may NOT state a result, a number, a price, a timeline or a capability",
                   "marked [link]", "APPROVED BRAND FACTS", "A post with no pitch sentence passes"):
        assert phrase in pitch, phrase
    assert "pitch_within_rules item is NOT hype" in rubric["no_filler_or_hype"]
    assert "pitch_within_rules item is acceptable" in rubric["operator_voice"]
    assert "pitch_within_rules" in fc.EDITOR_LINE_RULES and "pitch_within_rules" in rp.REPAIRABLE_EDITOR
    units, _ = fc.extract_post(CLEAN)
    assert "- pitch_within_rules:" in fc.build_editor_prompt(CLEAN, units, "")
    assert EM_DASH not in pitch


def test_a_pitch_sentence_that_breaks_the_rule_fails_and_is_repairable():
    post = dict(CLEAN, body_mdx=CLEAN["body_mdx"] + "\n\nBook a call and we will double your closed deals.")
    bad = unit(post, "Book a call and we")
    r = gate(post, [sup(post)], editor(pitch_within_rules=(bad.text, [bad.uid])))
    assert r["ok"] is False
    (f,) = [x for x in r["failures"] if x["rule"] == "editor:pitch_within_rules"]
    assert f["uid"] == bad.uid
    targets, skip = rp.plan([f], rp.containers(post), 5, 1 / 3)
    assert skip is None and list(targets) == [bad.uid]


def test_the_pitch_rule_does_not_exempt_a_sentence_from_banned_content():
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], banned=[
        {"id": "aipg_call_transfer_not_live", "label": "transfer is not live", "patterns": [r"\btransfer(?:s|red|ring)?\b"]}]))
    post = dict(CLEAN, body_mdx=CLEAN["body_mdx"] + "\n\nBook a call to hear how we transfer callers.")
    r = gate(post, [sup(post)], editor(), cfg=cfg)                 # the editor passes every item, pitch included
    assert r["ok"] is False
    assert any("banned content [aipg_call_transfer_not_live]" in v for v in r["violations"])


# ---------------------------------------------------------------------------
# 4. human-finish mode: on_hold: draft_pr
# ---------------------------------------------------------------------------

def held_gate(post):
    bad = unit(post, "Every dealer")
    style = unit(post, "The truth is")
    v1 = f'claim is not supported by its source: "{bad.text}" [source: {SRC}]'
    v2 = 'editor: no_filler_or_hype FAIL (There is no filler or hype) evidence: "The truth is that this matters."'
    return {"ok": False, "violations": [v1, v2, "editor: concrete_specific FAIL (x) evidence: \"\""], "error": None,
            "claims": [], "editor": [], "links": [], "model": "claude-opus-5-5", "llm_ran": True,
            "counts": {"claims": 3, "violations": 3},
            "failures": [{"uid": bad.uid, "where": "body", "surface": "body", "text": bad.text, "rule": "claim", "violation": v1},
                         {"uid": style.uid, "where": "body", "surface": "body", "text": style.text,
                          "rule": "editor:no_filler_or_hype", "violation": v2},
                         {"uid": "", "where": "", "surface": "", "text": "", "rule": "editor:concrete_specific",
                          "violation": "editor: concrete_specific FAIL (x) evidence: \"\""}]}


class Run:
    def __init__(self, cfg, fmt_violations=None, publish_error=None):
        self.cfg, self.calls, self.published = cfg, [], None
        self.fmt_violations = fmt_violations or []
        self.publish_error = publish_error
        self.never = mock.Mock(side_effect=AssertionError("must never run for a held post"))

    def _publish(self, repo, branch, files, title, body, token, **kw):
        self.calls.append("publish")
        if self.publish_error:
            raise self.publish_error
        self.published = {"repo": repo, "branch": branch, "files": files, "title": title, "body": body, "kw": kw}
        return "https://github.com/o/r/pull/77"

    def go(self, auto_merge=True):
        with mock.patch.object(se, "_brand_cfg", return_value=self.cfg), \
             mock.patch.object(se, "research_enabled", lambda c: True), \
             mock.patch.object(se, "build_source_pack", return_value=dict(PACK, mode="sourced")), \
             mock.patch.object(se, "generate_post", return_value=dict(POST)), \
             mock.patch.object(se, "revise_post", return_value=dict(POST)), \
             mock.patch.object(se, "assemble_mdx", side_effect=lambda p, d: ("MDX:" + p["body_mdx"], list(self.fmt_violations))), \
             mock.patch.object(se, "run_factcheck", side_effect=lambda post, cfg, **kw: held_gate(post)), \
             mock.patch.object(se, "publish_post", side_effect=self._publish), \
             mock.patch.object(se, "generate_images", self.never), \
             mock.patch.object(se, "merge_when_green", self.never), \
             mock.patch.object(se, "_distribute_social", self.never), \
             mock.patch.object(se, "_checkoff_topic", self.never):
            return se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-11-19", auto_merge=auto_merge)


def test_default_is_hold_and_a_held_post_opens_no_pull_request():
    r = Run(CFG)
    out = r.go()
    assert out["held"] is True and r.calls == [] and "draft_pr" not in out
    r.never.assert_not_called()
    se._load_cfg.cache_clear()
    full = se._load_cfg()
    assert all("on_hold" not in (b or {}) for b in full["brands"].values()), "not turned on for any brand"


def test_draft_pr_mode_keeps_the_held_post_as_a_draft_with_a_needs_editor_worklist():
    r = Run(dict(CFG, on_hold="draft_pr"))
    out = r.go()
    assert out["ok"] is False and out["held"] is True and out["published"] is False
    assert out["draft_pr"] == "https://github.com/o/r/pull/77" and len(out["violations"]) == 3
    p = r.published
    assert p["kw"] == {"draft": True} and p["title"] == "[NEEDS EDITOR] content: What must a dealer inventory?"
    assert p["branch"] == "slipstream-draft/inventory-2026-11-19"
    assert sorted(p["files"]) == ["src/content/blog/inventory.mdx", "src/content/blog/inventory.social.md"]
    assert not any(k.endswith(".png") for k in p["files"]), "no image is bought for a held post"
    body = p["body"]
    assert body.startswith("**NEEDS EDITOR. HELD by the fact-check gate. Draft only: do not merge")
    assert "## NEEDS EDITOR" in body and "This post did NOT pass the fact-check gate" in body
    assert "### Failing sentences (2)" in body
    assert "1. **[body]** Every dealer has already been fined for missing one.\n   - rule: claim\n" \
           f'   - cited quote: "{QUOTE}"' in body
    assert "2. **[body]** The truth is that this matters.\n   - rule: editor:no_filler_or_hype" in body
    assert "### Other reasons it was held (1)" in body and "editor: concrete_specific FAIL" in body
    assert "gates: passed" not in body and EM_DASH not in body


def test_a_held_draft_can_never_be_auto_merged_checked_off_or_sent_to_social():
    """auto_merge is asked for by the caller AND on in the brand config. The
    held draft still returns before any of it."""
    r = Run(dict(CFG, on_hold="draft_pr", auto_merge=True))
    out = r.go(auto_merge=True)
    r.never.assert_not_called()
    assert r.calls == ["publish"] and out["held"] is True and out["published"] is False
    assert "live_url" not in out and "social" not in out and "topic_checked_off" not in out and "pr_url" not in out


def test_draft_pr_mode_falls_back_to_a_plain_hold_when_no_draft_can_be_written():
    broken = Run(dict(CFG, on_hold="draft_pr"), fmt_violations=["missing required component: <PullQuote>"])
    out = broken.go()
    assert out["held"] is True and broken.calls == [], "a post that fails the format gate is not written anywhere"
    err = Run(dict(CFG, on_hold="draft_pr"), publish_error=RuntimeError("github 500"))
    out2 = err.go()
    assert out2["held"] is True and out2["draft_pr"] is None and "draft PR failed (RuntimeError: github 500)" in out2["draft_pr_note"]
    err.never.assert_not_called()
    none = se._held_draft_pr(dict(CFG, repo=None), POST, [], None, None, None, None, "shopify_article", "d", "t")
    assert none["draft_pr"] is None and "needs a repo brand" in none["draft_pr_note"]


def test_research_and_thin_pack_holds_never_open_a_draft():
    r = Run(dict(CFG, on_hold="draft_pr"))
    with mock.patch.object(se, "_brand_cfg", return_value=r.cfg), \
         mock.patch.object(se, "research_enabled", lambda c: True), \
         mock.patch.object(se, "build_source_pack", return_value=dict(PACK, mode="thin", usable_sources=1, min_sources=2)), \
         mock.patch.object(se, "generate_post", r.never), mock.patch.object(se, "publish_post", r.never):
        out = se.run_brand("autointelligence", topic="t", token="tok")
    assert out["reason"] == "thin_pack" and "draft_pr" not in out
    r.never.assert_not_called()


def test_publish_post_sends_draft_true_only_when_asked():
    sent = []

    def fake_http(method, url, token, body=None):
        sent.append((method, url, body))
        if method == "GET" and "/git/ref/" in url:
            return {"object": {"sha": "abc"}}
        if method == "GET":
            raise gh.PublishError("GET x -> 404: nope")
        return {"html_url": "https://github.com/o/r/pull/1"}
    gh.publish_post("o/r", "b", {"a.md": "x"}, "t", "body", "tok", http=fake_http, draft=True)
    gh.publish_post("o/r", "b", {"a.md": "x"}, "t", "body", "tok", http=fake_http)
    pulls = [b for m, u, b in sent if u.endswith("/pulls")]
    assert pulls[0]["draft"] is True and "draft" not in pulls[1]


def test_needs_editor_section_without_sentence_detail_still_lists_the_reasons():
    text = se.needs_editor_section(POST, ["factcheck gate error: boom"], {"failures": []}, None, CFG)
    assert "## NEEDS EDITOR" in text and "- factcheck gate error: boom" in text and "Failing sentences" not in text
