"""The targeted repair pass (services/slipstream_repair), the verdict
carry-forward, the editor naming its sentences, and the engine wiring.
All offline: fake HTTP, fake LLM. No test here makes a network or model call."""
import importlib.util
import os
from unittest import mock

import pytest

from services import slipstream_engine as se
from services import slipstream_factcheck as fc
from services import slipstream_repair as rp

EM_DASH = chr(0x2014)
SRC = "https://trade.example/report"
SRC_TEXT = ("Dealers must keep a written inventory of the customer data they hold. The survey covered "
            "1,200 stores across the country. " + "Background on the rule follows. " * 30)
QUOTE = "Dealers must keep a written inventory of the customer data they hold."

GOOD = f"Dealers must keep [a written inventory]({SRC}) of the customer data they hold."
OVERREACH = f"Every dealer in the country [has already been fined]({SRC}) for missing one."
FILLER = "The truth is that this matters more than you think."
NAV = "See [services](/services)."

MDX = "\n".join([
    f"<AnswerFirst>{GOOD}</AnswerFirst>",
    "",
    "## What does the rule ask for?",
    "",
    f"{GOOD} {OVERREACH} Start with the systems you already own.",
    "",
    "## Where should a dealer start?",
    "",
    f"{FILLER} List each system and who can open it. {NAV}",
])
POST = {"title": "What must a dealer inventory?", "description": "A plain answer for dealers.",
        "slug": "inventory", "body_mdx": MDX,
        "image_prompts": [{"name": "hero", "prompt": "h"}],
        "social": {"linkedin": "Every dealer has already been fined for missing one. Read the post.",
                   "x": "List each system and who can open it."}}
CFG = {"brand_key": "autointelligence", "domain": "automotiveintelligence.io", "business_key": "autointelligence",
       "repo": "o/r", "blog_dir": "src/content/blog", "money_pages": ["/services"],
       "factcheck": {"banned": [], "figure_allowlist": [], "repair_max_fail_ratio": 0.5, "repair_min_words": 0}}
PACK = {"sources": [{"url": SRC, "publisher": "Trade", "quotes": [{"text": QUOTE, "figures": []}]}],
        "brand_pages": [], "internal_links": ["/services"], "facts": ""}


class R:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.headers = status, text, {"content-type": "text/html"}


def http(url):
    return {SRC: R(200, f"<p>{SRC_TEXT}</p>"),
            "https://automotiveintelligence.io/services": R(200, "<p>" + "services " * 100 + "</p>")}.get(url) or R(404)


def uid(post, startswith):
    return next(u.uid for c in rp.containers(post) for u in c.units if u.text.startswith(startswith))


def failure(post, startswith, rule="claim", violation=None, **extra):
    u = next(u for c in rp.containers(post) for u in c.units if u.text.startswith(startswith))
    return {"uid": u.uid, "where": u.where, "surface": u.surface, "text": u.text, "rule": rule,
            "violation": violation or f"claim is not supported by its source: \"{u.text[:40]}\"", **extra}


def gate_result(post, *fails, claims=12):
    return {"ok": False, "violations": [f["violation"] for f in fails], "failures": list(fails),
            "claims": [], "counts": {"claims": claims}, "error": None}


def fields_llm(edits, seen=None):
    """Fake repair model: returns the given raw text per field id."""
    def _llm(system, user):
        assert system == rp.REPAIR_SYSTEM
        if seen is not None:
            seen.append(user)
        return {"fields": [{"id": k, "text": v} for k, v in edits.items()]}
    return _llm


def cid(post, startswith):
    return next(c.cid for c in rp.containers(post) if any(u.text.startswith(startswith) for u in c.units))


BODY_FIELD = f"{GOOD} {OVERREACH} Start with the systems you already own."
START_FIELD = f"{FILLER} List each system and who can open it. {NAV}"


def std_fails(post=POST):
    return [failure(post, "Every dealer in the country"),
            failure(post, "Every dealer has already", violation="unverifiable claim: \"Every dealer has already been fined\""),
            failure(post, "The truth is", rule="editor:no_filler_or_hype",
                    violation="editor: no_filler_or_hype FAIL (...) evidence: \"The truth is that this matters\"")]


def good_edits(post=POST):
    return {cid(post, "Every dealer in the country"): f"{GOOD} Start with the systems you already own.",
            cid(post, "Every dealer has already"): "Read the post.",
            cid(post, "The truth is"): f"List each system and who can open it. {NAV}"}


def run(post=POST, fails=None, edits=None, cfg=CFG, pack=PACK, seen=None, claims=12):
    fails = std_fails(post) if fails is None else fails
    edits = good_edits(post) if edits is None else edits
    return rp.repair_post(post, gate_result(post, *fails, claims=claims), cfg, pack=pack,
                          llm=fields_llm(edits, seen))


# ---------------------------------------------------------------------------
# containers: every sentence maps back to one raw field
# ---------------------------------------------------------------------------

BLOCK_POST = {
    "title": "How do I set my service area?", "description": "A plain answer. Two sentences here.",
    "slug": "s", "heroAlt": "a map on a desk",
    "faq": [{"q": "How far can it reach?", "a": "About 2 hours of driving. Keep it honest."}],
    "body": [
        {"type": "answer", "text": "Set the area you really serve. Keep it tight."},
        {"type": "definition", "term": "Service area", "text": "The places you travel to."},
        {"type": "h2", "text": "How far should it reach?"},
        {"type": "p", "text": "Google says about 2 hours of driving time. Most owners set it far too wide."},
        {"type": "callout", "title": "One rule", "text": "Match the area to the work you take."},
        {"type": "ul", "items": ["List the towns you serve.", "Drop the ones you do not."]},
        {"type": "table", "headers": ["Town", "Served"], "rows": [["Plano", "yes"], ["Waco", "no"]]},
        {"type": "image", "src": "/blog/s-map.png", "alt": "a map", "caption": "A tight service area."},
        {"type": "stat", "value": "2 hours", "label": "driving time limit", "source": "Google", "href": SRC},
        {"type": "quote", "text": "Serve the area you can reach."},
        {"type": "h2", "text": "What next?"},
        {"type": "p", "text": "Request a quote to talk it through."},
        {"type": "links", "title": "Next", "items": [{"label": "Quote", "href": "/quote"}]},
        {"type": "sources", "title": "Sources", "items": [{"label": "Google", "href": SRC}]},
    ],
    "image_prompts": [{"name": "hero", "prompt": "h"}],
    "social": {"linkedin": "Most owners set it far too wide. Read the post.", "x": "Keep it tight."},
}


@pytest.mark.parametrize("post", [POST, BLOCK_POST])
def test_containers_cover_every_sentence_in_gate_order_with_the_gates_ids(post):
    units, _ = fc.extract_post(post)
    flat = [u for c in rp.containers(post) for u in c.units]
    assert [(u.uid, u.text, u.links, u.where) for u in flat] == [(u.uid, u.text, u.links, u.where) for u in units]
    assert len({c.cid for c in rp.containers(post)}) == len(rp.containers(post))


def test_meta_fields_are_not_editable_and_stats_and_table_rows_are_delete_only():
    by_where = {}
    for c in rp.containers(BLOCK_POST):
        for u in c.units:
            by_where.setdefault(u.where, c)
    for where in ("title", "image alt", "hero alt", "faq question", "links block", "sources block"):
        assert by_where[where].editable is False, where
    assert by_where["stat"].delete_only and by_where["table row"].delete_only
    assert by_where["p"].editable and not by_where["p"].delete_only


def test_a_post_the_two_walks_disagree_on_cannot_be_repaired():
    with mock.patch.object(fc, "extract_post", return_value=([fc.Unit(text="something else")], [])):
        with pytest.raises(rp.RepairError, match="could not map"):
            rp.containers(POST)
    with pytest.raises(rp.RepairError):
        rp.containers({"title": "t"})


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------

def test_repair_rewrites_or_deletes_only_the_failing_sentences():
    out = run()
    assert out["ran"] and out["ok"] is True and out["violations"] == [] and out["targets"] == 3
    body = out["post"]["body_mdx"]
    assert "has already been fined" not in body and "The truth is" not in body
    assert f"{GOOD} Start with the systems you already own." in body
    assert f"List each system and who can open it. {NAV}" in body
    assert out["post"]["social"] == {"linkedin": "Read the post.", "x": "List each system and who can open it."}
    assert POST["body_mdx"] == MDX, "the input post is not mutated"
    acts = sorted((c["action"], c["where"], c["before"][:12]) for c in out["changes"])
    assert acts == [("deleted", "body", "Every dealer"), ("deleted", "body", "The truth is"),
                    ("deleted", "social linkedin", "Every dealer")]
    assert out["changes"][0]["rules"]


def test_a_rewrite_is_recorded_with_before_and_after():
    rewrite = f"Dealers [may face penalties]({SRC}) for missing one."
    edits = good_edits()
    edits[cid(POST, "Every dealer in the country")] = f"{GOOD} {rewrite} Start with the systems you already own."
    edits[cid(POST, "Every dealer has already")] = "Dealers may face penalties for missing one. Read the post."
    out = run(edits=edits)
    assert out["ok"] is True, out["violations"]
    rw = next(c for c in out["changes"] if c["action"] == "rewritten" and c["where"] == "body")
    assert rw["before"].startswith("Every dealer in the country") and rw["after"] == "Dealers may face penalties for missing one."
    assert rw["rules"] == ["claim"] and rw["where"] == "body"


def test_prompt_holds_only_the_failing_fields_their_violations_and_their_quotes():
    seen = []
    run(seen=seen)
    prompt = seen[0]
    assert prompt.count("=== FIELD ") == 3
    assert "RAW TEXT:\n" + BODY_FIELD in prompt and START_FIELD in prompt
    assert "## What does the rule ask for?" not in prompt and "<AnswerFirst>" not in prompt, "passing fields are not sent"
    assert f"FAILING SENTENCE {uid(POST, 'Every dealer in the country')} GROUP G1: Every dealer in the country" in prompt
    assert f"FAILING SENTENCE {uid(POST, 'Every dealer has already')} GROUP G1: Every dealer has already" in prompt
    assert "VIOLATION (claim): claim is not supported by its source" in prompt
    assert "VIOLATION (editor:no_filler_or_hype): editor: no_filler_or_hype FAIL" in prompt
    assert f'QUOTE: "{QUOTE}"' in prompt
    assert "QUOTE: none available. Delete it" in prompt, "a sentence with no cited source gets no quote"
    assert EM_DASH not in prompt + rp.REPAIR_SYSTEM


def test_second_hand_quotes_are_never_offered_to_the_repair():
    pack = dict(PACK, sources=[{"url": SRC, "publisher": "Trade", "quotes": [
        {"text": QUOTE, "figures": []},
        {"text": "According to Gartner, 90 percent of dealers were fined.", "figures": ["90 percent"],
         "second_hand": "Gartner", "original_in_pack": False}]}])
    seen = []
    run(pack=pack, seen=seen)
    assert "Gartner" not in seen[0] and QUOTE in seen[0]


# ---------------------------------------------------------------------------
# the contract: code rejects, whatever the model returned
# ---------------------------------------------------------------------------

def bad(edit_for, text, **kw):
    edits = good_edits()
    edits[cid(POST, edit_for)] = text
    return run(edits=edits, **kw)


def test_changing_a_sentence_that_passed_is_rejected():
    out = bad("Every dealer in the country", f"{GOOD} Begin with the systems you already own.")
    assert out["ok"] is False and out["post"] is POST
    assert any(v.startswith("repair changed a sentence that had passed (body)") and "Start with the systems" in v
               for v in out["violations"])


def test_removing_a_sentence_that_passed_is_rejected():
    out = bad("Every dealer in the country", GOOD)
    assert out["ok"] is False
    assert any("repair removed a sentence that had passed" in v and "Start with the systems" in v
               for v in out["violations"])


def test_touching_the_link_of_a_passing_sentence_is_rejected():
    out = bad("Every dealer in the country",
              "Dealers must keep a written inventory of the customer data they hold. Start with the systems you already own.")
    assert out["ok"] is False and any("sentence that had passed" in v for v in out["violations"])


def test_adding_a_sentence_is_rejected():
    out = bad("Every dealer in the country",
              f"{GOOD} Start with the systems you already own. Then call your vendor.")
    assert out["ok"] is False and any('repair added a sentence: "Then call your vendor."' in v for v in out["violations"])


def test_a_rewrite_may_not_split_into_more_sentences():
    out = bad("Every dealer in the country",
              f"{GOOD} Dealers can be fined. Fines are possible. Start with the systems you already own.")
    assert out["ok"] is False and any("turned 1 sentence(s) into 2" in v for v in out["violations"])


def test_a_new_figure_is_rejected():
    out = bad("Every dealer in the country",
              f"{GOOD} About [40 percent of dealers]({SRC}) were fined. Start with the systems you already own.")
    assert out["ok"] is False
    assert any("repair introduced a figure the sentence did not have (40 percent)" in v for v in out["violations"])


def test_a_new_link_or_url_is_rejected():
    out = bad("Every dealer in the country",
              f"{GOOD} Dealers [can be fined](https://other.example/fines). Start with the systems you already own.")
    assert out["ok"] is False
    assert any("introduced a link the sentence did not have: https://other.example/fines" in v for v in out["violations"])
    assert any("introduced a URL that was not in the post" in v for v in out["violations"])
    internal = bad("The truth is", f"See [pricing](/pricing). List each system and who can open it. {NAV}")
    assert internal["ok"] is False and any("/pricing" in v for v in internal["violations"])


def test_a_figure_the_failing_sentence_already_had_may_stay():
    post = dict(POST, body_mdx=MDX.replace(OVERREACH, f"All [1,200 stores]({SRC}) were fined last year."))
    fails = [failure(post, "All 1,200 stores")]
    edits = {cid(post, "All 1,200 stores"):
             f"{GOOD} The survey covered [1,200 stores]({SRC}). Start with the systems you already own."}
    out = run(post=post, fails=fails, edits=edits)
    assert out["ok"] is True, out["violations"]
    assert out["changes"] == [{"action": "rewritten", "where": "body", "before": "All 1,200 stores were fined last year.",
                               "after": "The survey covered 1,200 stores.", "rules": ["claim"]}]


def test_a_field_that_was_not_sent_cannot_be_edited():
    edits = good_edits()
    edits[cid(POST, "What must a dealer")] = "A better title"
    out = run(edits=edits)
    assert out["ok"] is False and "was not sent for repair" in out["violations"][0]


def test_malformed_reply_model_error_and_em_dash_all_hold():
    gr = gate_result(POST, *std_fails())
    for llm, needle in [(lambda s, u: {"nope": 1}, "no 'fields' list"),
                        (lambda s, u: {"fields": [{"id": "C1"}]}, "malformed field"),
                        (mock.Mock(side_effect=RuntimeError("LLM 529")), "RuntimeError: LLM 529")]:
        out = rp.repair_post(POST, gr, CFG, pack=PACK, llm=llm)
        assert out["ok"] is False and out["post"] is POST and needle in out["violations"][0]
        assert out["violations"][0].startswith("repair pass failed (post HELD)")
    dash = bad("The truth is", f"List each system {EM_DASH} and who can open it. {NAV}")
    assert dash["ok"] is False and "em-dash" in dash["violations"][0]


def test_a_repair_that_changes_nothing_is_not_a_repair():
    out = run(edits={cid(POST, "Every dealer in the country"): BODY_FIELD,
                     cid(POST, "Every dealer has already"): POST["social"]["linkedin"],
                     cid(POST, "The truth is"): START_FIELD})
    assert out["ok"] is False and out["violations"] == ["repair changed nothing"]


# ---------------------------------------------------------------- structure

def test_deleting_a_whole_section_body_leaves_a_heading_without_body_and_holds():
    post = dict(POST, body_mdx=MDX.replace(START_FIELD, FILLER))
    out = run(post=post, fails=[failure(post, "The truth is", rule="editor:no_filler_or_hype")],
              edits={cid(post, "The truth is"): ""})
    assert out["ok"] is False
    assert out["violations"] == ['repair left the post broken: heading has no body under it: "## Where should a dealer start?"']


def test_an_emptied_component_or_link_holds():
    post = dict(POST, body_mdx=MDX + f"\n\n<Callout>{FILLER}</Callout>")
    fails = [failure(post, "The truth is", rule="editor:no_filler_or_hype")]
    body_cid = next(c.cid for c in rp.containers(post) if c.raw.startswith("<Callout>"))
    other = next(c.cid for c in rp.containers(post) if c.raw == START_FIELD)
    fails_both = [f for f in (failure(post, "The truth is", rule="editor:no_filler_or_hype"),)]
    out = rp.repair_post(post, gate_result(post, *fails_both), CFG, pack=PACK,
                         llm=fields_llm({other: START_FIELD.replace(FILLER + " ", "")}))
    assert out["ok"] is True                      # the first copy can go cleanly
    # now target the Callout copy and return an emptied component
    units = [u for c in rp.containers(post) for u in c.units if u.text == FILLER]
    f2 = {"uid": units[1].uid, "where": units[1].where, "surface": units[1].surface, "text": FILLER,
          "rule": "editor:no_filler_or_hype", "violation": "editor: no_filler_or_hype FAIL"}
    out2 = rp.repair_post(post, gate_result(post, f2), CFG, pack=PACK,
                          llm=fields_llm({body_cid: "<Callout></Callout>"}))
    assert out2["ok"] is False
    assert "repair left the post broken: an emptied <Callout> component was left in the body" in out2["violations"]
    assert rp.structure_problems(POST, dict(POST, body_mdx=MDX.replace("[services](/services)", "[](/services)"))) == [
        "repair left the post broken: a link was left with no link text"]
    assert fails


def block_fail(startswith, rule="claim"):
    return failure(BLOCK_POST, startswith, rule=rule)


def block_run(fails, edits, cfg=CFG):
    return rp.repair_post(BLOCK_POST, gate_result(BLOCK_POST, *fails), cfg, pack=PACK, llm=fields_llm(edits))


def test_block_format_repair_edits_one_sentence_and_keeps_the_rest():
    c = cid(BLOCK_POST, "Google says about 2 hours")
    out = block_run([block_fail("Most owners set it far too wide", "editor:no_unsupported_absolutes")],
                    {c: "Google says about 2 hours of driving time."})
    assert out["ok"] is True, out["violations"]
    assert out["post"]["body"][3] == {"type": "p", "text": "Google says about 2 hours of driving time."}
    assert out["post"]["body"][:3] == BLOCK_POST["body"][:3] and out["post"]["faq"] == BLOCK_POST["faq"]


def test_block_format_heading_without_body_holds():
    c = cid(BLOCK_POST, "Request a quote")
    out = block_run([block_fail("Request a quote")], {c: ""})
    assert out["ok"] is False
    assert out["violations"] == ['repair left the post broken: heading has no body under it: "What next?"']


def test_faq_question_without_an_answer_holds():
    post = dict(BLOCK_POST, faq=[{"q": "How far can it reach?", "a": "Most owners set it far too wide."}])
    f = failure(post, "Most owners set it far too wide", rule="editor:no_unsupported_absolutes")
    faq_unit = [u for c in rp.containers(post) for u in c.units
                if u.where == "faq answer" and u.text.startswith("Most owners")][0]
    f.update(uid=faq_unit.uid, where="faq answer", surface="secondary")
    faq_cid = next(c.cid for c in rp.containers(post) if c.path == ("faq", 0, "a"))
    out = rp.repair_post(post, gate_result(post, f), CFG, pack=PACK, llm=fields_llm({faq_cid: ""}))
    assert out["ok"] is False
    assert out["violations"] == ['repair left the post broken: FAQ question 1 has no answer: "How far can it reach?"']


def test_emptying_the_answer_block_the_description_or_a_social_draft_holds():
    for path, needle in [(("body", 0, "text"), "the answer-first block is missing or empty"),
                         (("description",), "the description is empty"),
                         (("social", "x"), "the x social draft is empty")]:
        c = next(c for c in rp.containers(BLOCK_POST) if c.path == path)
        fails = [{"uid": u.uid, "where": u.where, "surface": u.surface, "text": u.text, "rule": "claim",
                  "violation": "v"} for u in c.units]
        out = block_run(fails, {c.cid: ""})
        assert out["ok"] is False and f"repair left the post broken: {needle}" in out["violations"], path


def test_a_stat_block_or_table_row_can_only_be_deleted():
    stat = next(c for c in rp.containers(BLOCK_POST) if c.path == ("body", 8))
    f = [{"uid": stat.units[0].uid, "where": "stat", "surface": "body", "text": stat.units[0].text,
          "rule": "figure", "violation": "figure \"2 hours\" does not appear in the cited source"}]
    reworded = block_run(f, {stat.cid: "about 3 hours"})
    assert reworded["ok"] is False and "delete-only (a stat or a table row)" in reworded["violations"][0]
    deleted = block_run(f, {stat.cid: ""})
    assert deleted["ok"] is True and not any(b.get("type") == "stat" for b in deleted["post"]["body"])
    assert deleted["changes"][0]["action"] == "deleted" and deleted["changes"][0]["where"] == "stat"


def test_a_list_item_can_be_deleted_and_an_emptied_list_is_dropped():
    one = cid(BLOCK_POST, "Drop the ones")
    out = block_run([block_fail("Drop the ones")], {one: ""})
    assert out["ok"] is True and out["post"]["body"][5]["items"] == ["List the towns you serve."]
    both = block_run([block_fail("Drop the ones"), block_fail("List the towns")],
                     {one: "", cid(BLOCK_POST, "List the towns"): ""})
    assert both["ok"] is True and not any(b.get("type") == "ul" for b in both["post"]["body"])


def test_repair_that_leaves_the_post_under_its_minimum_length_holds():
    words = rp._body_words(POST)
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], repair_min_words=words - 3))
    out = run(cfg=cfg)
    assert out["ok"] is False
    assert any(v.startswith("repair left the post under its minimum length") for v in out["violations"])
    assert run(cfg=dict(CFG, factcheck=dict(CFG["factcheck"], repair_min_words=10)))["ok"] is True


# ---------------------------------------------------------------- banned

BAN_CFG = dict(CFG, factcheck=dict(CFG["factcheck"], banned=[
    {"id": "aipg_call_transfer_not_live", "label": "call transfer is not live",
     "patterns": [r"\btransfer(?:s|red|ring)?\b"]}]))
BANNED = "Our assistant transfers the caller to your front desk."


def banned_post():
    return dict(POST, body_mdx=MDX.replace(FILLER, BANNED))


def banned_fail(post):
    return failure(post, "Our assistant transfers", rule="banned", banned_id="aipg_call_transfer_not_live",
                   violation='banned content [aipg_call_transfer_not_live] call transfer is not live')


def test_a_banned_hit_cannot_be_rescued_by_rewording_around_the_pattern():
    """The reworded sentence no longer matches the regex, and still means the
    banned thing. The contract does not look at the regex: banned = delete."""
    post = banned_post()
    reworded = "Our assistant hands the caller over to your front desk."
    assert fc.check_banned([fc.Unit(text=reworded)], BAN_CFG["factcheck"]["banned"]) == []
    out = run(post=post, fails=[banned_fail(post)], cfg=BAN_CFG,
              edits={cid(post, "Our assistant transfers"): f"{reworded} List each system and who can open it. {NAV}"})
    assert out["ok"] is False and out["post"] is post
    assert out["violations"] == ['banned content may only be deleted, not reworded: "' + BANNED + '"']


def test_a_banned_hit_may_be_deleted_and_the_prompt_says_delete_only():
    post = banned_post()
    seen = []
    out = run(post=post, fails=[banned_fail(post)], cfg=BAN_CFG, seen=seen,
              edits={cid(post, "Our assistant transfers"): f"List each system and who can open it. {NAV}"})
    assert out["ok"] is True and "transfers" not in out["post"]["body_mdx"]
    assert "DELETE ONLY: Our assistant transfers" in seen[0] and "QUOTE" not in seen[0].split("DELETE ONLY")[1]


# ---------------------------------------------------------------- copies

def test_copies_of_the_same_claim_are_grouped():
    units = [u for c in rp.containers(POST) for u in c.units]
    pick = [u for u in units if u.text.startswith(("Every dealer", "The truth is"))]
    groups = rp.group_copies(pick)
    assert groups == [[uid(POST, "Every dealer in the country"), uid(POST, "Every dealer has already")]]


def test_copies_must_get_the_same_fix():
    edits = good_edits()
    edits[cid(POST, "Every dealer has already")] = POST["social"]["linkedin"]       # body deleted, social kept
    out = run(edits=edits)
    assert out["ok"] is False
    assert any(v.startswith("copies of the same claim were not repaired the same way") for v in out["violations"])
    both = good_edits()
    both[cid(POST, "Every dealer in the country")] = (
        f"{GOOD} Dealers [may face penalties]({SRC}) for missing one. Start with the systems you already own.")
    both[cid(POST, "Every dealer has already")] = "Dealers may face penalties for missing one. Read the post."
    assert run(edits=both)["ok"] is True, "both rewritten is consistent"


# ---------------------------------------------------------------------------
# when the pass does not run at all
# ---------------------------------------------------------------------------

def test_too_many_failing_sentences_skips_repair():
    llm = mock.Mock()
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], repair_max_fail_ratio=1 / 3))
    out = rp.repair_post(POST, gate_result(POST, *std_fails(), claims=6), cfg, pack=PACK, llm=llm)
    llm.assert_not_called()
    assert out["ran"] is False and out["ok"] is False and out["post"] is POST
    assert out["skipped"] == ("3 failing sentences against 6 judged claims is more than the repair limit "
                              "(33%); a post this far off is held, not patched")
    ok = rp.repair_post(POST, gate_result(POST, *std_fails(), claims=9), cfg, pack=PACK, llm=fields_llm(good_edits()))
    assert ok["ok"] is True, "exactly a third is still repairable"


def test_threshold_falls_back_to_sentences_when_no_claim_was_judged():
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], repair_max_fail_ratio=0.1))
    out = rp.repair_post(POST, gate_result(POST, *std_fails(), claims=0), cfg, pack=PACK, llm=mock.Mock())
    assert "failing sentences against" in out["skipped"] and "sentences is more than" in out["skipped"]


@pytest.mark.parametrize("rid", ["answers_title", "concrete_specific"])
def test_an_editor_fail_about_what_the_post_lacks_skips_repair(rid):
    f = {"uid": "", "where": "", "surface": "", "text": "", "rule": f"editor:{rid}", "violation": "v"}
    out = rp.repair_post(POST, gate_result(POST, f), CFG, pack=PACK, llm=mock.Mock())
    assert out["ran"] is False and "a repair may not add anything" in out["skipped"]


def test_failures_that_cannot_be_tied_to_an_editable_sentence_skip_repair():
    llm = mock.Mock()
    no_unit = {"uid": "", "where": "", "surface": "", "text": "", "rule": "claim", "violation": "v"}
    stale = dict(failure(POST, "The truth is"), text="a different sentence")
    title = failure(POST, "What must a dealer", rule="banned")
    for f, needle in [(no_unit, "could not be tied to one sentence"), (stale, "could not be tied to one sentence"),
                      (title, "sits in the title")]:
        out = rp.repair_post(POST, gate_result(POST, f), CFG, pack=PACK, llm=llm)
        assert out["ran"] is False and needle in out["skipped"]
    assert rp.repair_post(POST, gate_result(POST), CFG, llm=llm)["skipped"].startswith("the gate reported no")
    llm.assert_not_called()


# ---------------------------------------------------------------------------
# the gate tells the repair which sentence broke which rule
# ---------------------------------------------------------------------------

def editor_reply(**fails):
    items = []
    for rid, _q, needs in fc.EDITOR_RUBRIC:
        if rid in fails:
            items.append({"id": rid, "pass": False, **fails[rid]})
        else:
            items.append({"id": rid, "pass": True, "evidence": "Dealers must keep a written inventory of "
                          "the customer data they hold." if needs else "", "sentence_ids": []})
    return {"items": items}


def gate(post, claims, ed=None, cache=None, seen=None, cfg=CFG, **kw):
    def llm(system, user):
        if seen is not None:
            seen.append((system, user))
        return {"claims": claims} if system == fc.VERIFIER_SYSTEM else (ed or editor_reply())
    return fc.run_gate(post, cfg, http_get=http, llm=llm, facts_text="", verdict_cache=cache, **kw)


def supported(post, startswith):
    return {"sentence_id": uid(post, startswith), "claim": "keeps an inventory", "kind": "external",
            "source": SRC, "verdict": "SUPPORTED", "quote": QUOTE}


def test_every_kind_of_failure_names_its_sentence_and_its_rule():
    post = dict(POST, body_mdx=MDX + "\n\nAbout 40 percent of dealers skip it. See [this](https://dead.example/x).")
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], banned=[{"id": "no_fines", "label": "x", "patterns": ["fined"]}]))
    claims = [{"sentence_id": uid(post, "Every dealer in the country"), "claim": "all fined", "kind": "external",
               "source": SRC, "verdict": "NOT_SUPPORTED", "quote": ""}]
    filler = uid(post, "The truth is")
    ed = editor_reply(no_filler_or_hype={"evidence": "wrong text", "sentence_ids": [filler, "S999"]},
                      operator_voice={"evidence": "List each system and who can open it."})
    r = gate(post, claims, ed, cfg=cfg)
    got = {(f["rule"], f["text"][:18]) for f in r["failures"]}
    assert ("claim", "Every dealer in th") in got
    assert ("banned", "Every dealer in th") in got and ("banned", "Every dealer has a") in got
    assert ("figure", "About 40 percent o") in got
    assert ("link", "See this.") in got
    assert ("editor:no_filler_or_hype", "The truth is that ") in got, "named by sentence_ids"
    assert ("editor:operator_voice", "List each system a") in got, "found from the evidence line"
    assert all(f["uid"] and f["violation"] for f in r["failures"])
    ed_rec = {e["id"]: e for e in r["editor"]}
    assert ed_rec["no_filler_or_hype"]["sentence_ids"] == [filler]


def test_editor_prompt_carries_sentence_ids_and_asks_for_every_offending_line():
    units, _ = fc.extract_post(POST)
    prompt = fc.build_editor_prompt(POST, units, "")
    u = next(x for x in units if x.text.startswith("The truth is"))
    assert f"{u.uid} [body] {FILLER}" in prompt
    assert '"sentence_ids"' in fc.EDITOR_SYSTEM and "EVERY line that breaks that rule" in fc.EDITOR_SYSTEM
    assert fc.strip_prompt_markers(f"{u.uid} [body] {FILLER} [link]") == FILLER
    assert EM_DASH not in fc.EDITOR_SYSTEM


def test_an_editor_fail_about_the_whole_post_names_no_sentence():
    r = gate(POST, [], editor_reply(concrete_specific={"evidence": "", "sentence_ids": []}))
    (f,) = [x for x in r["failures"] if x["rule"] == "editor:concrete_specific"]
    assert f["uid"] == "" and f["text"] == ""


# ---------------------------------------------------------------------------
# carry-forward of verified sentences
# ---------------------------------------------------------------------------

CLEAN = dict(POST, body_mdx=f"<AnswerFirst>{GOOD}</AnswerFirst>\n\nStart with the systems you already own. {NAV}",
             social={"linkedin": "Read the post."})


def test_a_supported_sentence_is_not_judged_again_while_it_is_identical():
    cache, seen = {}, []
    r1 = gate(CLEAN, [supported(CLEAN, "Dealers must keep")], cache=cache, seen=seen)
    assert r1["ok"] is True and len(cache) == 1 and r1["counts"]["claims_carried"] == 0
    # second pass: the verifier now (wrongly) rejects it. It is never asked.
    flip = [{**supported(CLEAN, "Dealers must keep"), "verdict": "NOT_SUPPORTED", "quote": ""}]
    seen.clear()
    r2 = gate(CLEAN, flip, cache=cache, seen=seen)
    verifier_prompt = seen[0][1]
    assert seen[0][0] == fc.VERIFIER_SYSTEM
    assert "Dealers must keep a written inventory of the customer data they hold. LINKS" not in verifier_prompt
    assert "Start with the systems you already own." in verifier_prompt, "unjudged sentences are still sent"
    assert r2["ok"] is True and r2["counts"]["claims_carried"] == 1 and r2["carried"] == 1
    assert r2["claims"][0]["verdict"] == "SUPPORTED" and r2["claims"][0]["carried"] is True
    assert f"=== SOURCE {SRC} ===" not in verifier_prompt, "a source no remaining sentence can use is not resent"


def test_without_a_cache_every_pass_judges_everything():
    seen = []
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], seen=seen)
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], seen=seen)
    assert all("Dealers must keep a written inventory" in u for s, u in seen if s == fc.VERIFIER_SYSTEM)


def test_carried_verdict_is_dropped_the_moment_the_sentence_changes():
    cache, seen = {}, []
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], cache=cache)
    changed = dict(CLEAN, body_mdx=CLEAN["body_mdx"].replace("of the customer data they hold", "of all customer data"))
    r = gate(changed, [], cache=cache, seen=seen)
    assert "Dealers must keep a written inventory of all customer data." in seen[0][1]
    assert r["counts"]["claims_carried"] == 0 and r["carried"] == 0


def test_carried_verdict_is_dropped_the_moment_the_link_changes():
    cache, seen = {}, []
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], cache=cache)
    other = "https://other.example/page"
    relinked = dict(CLEAN, body_mdx=CLEAN["body_mdx"].replace(SRC, other))

    def http2(url):
        return R(200, "<p>" + "Something else entirely. " * 40 + "</p>") if url == other else http(url)
    r = fc.run_gate(relinked, CFG, http_get=http2, facts_text="", verdict_cache=cache,
                    llm=lambda s, u: (seen.append(u) or {"claims": []}) if s == fc.VERIFIER_SYSTEM else editor_reply())
    assert f"LINKS: {other}" in seen[0] and r["carried"] == 0


def test_carried_verdict_is_dropped_when_the_source_no_longer_holds_the_quote():
    cache, seen = {}, []
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], cache=cache)

    def http2(url):
        return R(200, "<p>" + "The page was rewritten. " * 40 + "</p>") if url == SRC else http(url)
    r = fc.run_gate(CLEAN, CFG, http_get=http2, facts_text="", verdict_cache=cache,
                    llm=lambda s, u: (seen.append(u) or {"claims": []}) if s == fc.VERIFIER_SYSTEM else editor_reply())
    assert "Dealers must keep a written inventory" in seen[0] and r["carried"] == 0


def test_a_sentence_with_any_unsupported_claim_is_never_carried_and_a_later_fail_evicts_it():
    cache = {}
    two = [supported(CLEAN, "Dealers must keep"),
           {**supported(CLEAN, "Dealers must keep"), "claim": "second", "verdict": "UNVERIFIABLE", "quote": ""}]
    gate(CLEAN, two, cache=cache)
    assert cache == {}
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], cache=cache)
    assert len(cache) == 1
    made_up = [{**supported(CLEAN, "Dealers must keep"), "quote": "A passage the page does not contain at all."}]
    fresh = {}
    gate(CLEAN, made_up, cache=fresh)
    assert fresh == {}, "a SUPPORTED verdict whose quote code could not find is not remembered"


def test_when_every_judged_sentence_is_carried_the_verifier_is_not_called():
    post = dict(CLEAN, body_mdx=f"<AnswerFirst>{GOOD}</AnswerFirst>", description="", social={})
    post = {k: v for k, v in post.items() if k != "title"}
    cache, seen = {}, []
    gate(post, [supported(post, "Dealers must keep")], cache=cache)
    r = gate(post, [], cache=cache, seen=seen)
    assert [s for s, _ in seen] == [fc.EDITOR_SYSTEM] and r["ok"] is True and r["counts"]["claims"] == 1


def test_a_stray_new_verdict_on_a_carried_sentence_is_ignored():
    cache = {}
    gate(CLEAN, [supported(CLEAN, "Dealers must keep")], cache=cache)
    stray = [{**supported(CLEAN, "Dealers must keep"), "verdict": "NOT_SUPPORTED", "quote": ""}]
    r = gate(CLEAN, stray, cache=cache)
    assert r["ok"] is True and [c["verdict"] for c in r["claims"]] == ["SUPPORTED"]
    assert not [f for f in r["failures"] if f["rule"] == "claim"]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_repair_pass_is_on_by_default_in_the_real_config_and_a_brand_can_turn_it_off():
    se._load_cfg.cache_clear()
    for brand in ("autointelligence", "aiphoneguy", "worshipdigital"):
        f = se._brand_cfg(brand)["factcheck"]
        assert f["repair_pass"] is True and 0.3 < f["repair_max_fail_ratio"] < 0.35 and f["repair_min_words"] == 600
    full = {"factcheck": {"defaults": {"repair_pass": True, "repair_max_fail_ratio": 0.5},
                          "brands": {"off": {"repair_pass": False}, "tight": {"repair_max_fail_ratio": 0.1}}}}
    assert fc.resolve_factcheck_cfg(full, "off")["repair_pass"] is False
    assert fc.resolve_factcheck_cfg(full, "other")["repair_pass"] is True
    assert fc.resolve_factcheck_cfg(full, "tight")["repair_max_fail_ratio"] == 0.1
    assert fc.resolve_factcheck_cfg({}, "x")["repair_pass"] is True
    assert abs(fc.resolve_factcheck_cfg({}, "x")["repair_max_fail_ratio"] - 1 / 3) < 1e-9
    assert fc.resolve_factcheck_cfg({"factcheck": {"defaults": {"repair_pass": False}}}, "x")["repair_pass"] is False


# ---------------------------------------------------------------------------
# engine wiring
# ---------------------------------------------------------------------------

def _g(ok, violations=(), failures=(), claims=()):
    return {"ok": ok, "violations": list(violations), "error": None, "claims": list(claims), "editor": [],
            "links": [], "model": "claude-opus-5-5", "llm_ran": True, "failures": list(failures),
            "counts": {"claims": 12, "violations": len(violations)}}


REPAIRED = dict(POST, body_mdx=MDX.replace(" " + OVERREACH, ""))
CHANGE = {"action": "deleted", "where": "body", "before": "Every dealer in the country has already been fined | for missing one.",
          "after": "", "rules": ["claim"]}


class Eng:
    def __init__(self, gates, repair=None, cfg=None, fmt=None):
        self.order, self.gate_kw, self.gate_posts, self.repair_args = [], [], [], None
        self.gates = list(gates)
        self.repair = repair
        self.cfg = cfg or CFG
        self.fmt = list(fmt or [[]])

    def _gate(self, post, cfg, **kw):
        self.order.append("gate")
        self.gate_kw.append(kw)
        self.gate_posts.append(post)
        return self.gates.pop(0)

    def _repair(self, post, gate_result, cfg, **kw):
        self.order.append("repair")
        self.repair_args = (post, gate_result, kw)
        return dict(self.repair)

    def _assemble(self, post, date_str):
        self.order.append("assemble")
        v = self.fmt.pop(0) if len(self.fmt) > 1 else self.fmt[0]
        return f"MDX:{post['body_mdx']}", v

    def _publish(self, repo, branch, files, title, body, token):
        self.order.append("publish")
        self.files, self.pr_body = files, body
        return "https://gh/pull/1"

    def run(self):
        with mock.patch.object(se, "_brand_cfg", return_value=self.cfg), \
             mock.patch.object(se, "generate_post", return_value=dict(POST)), \
             mock.patch.object(se, "revise_post", side_effect=lambda *a, **k: self.order.append("revise") or dict(POST)), \
             mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
             mock.patch.object(se, "assemble_mdx", side_effect=self._assemble), \
             mock.patch.object(se, "run_factcheck", side_effect=self._gate), \
             mock.patch.object(se, "repair_post", side_effect=self._repair), \
             mock.patch.object(se, "publish_post", side_effect=self._publish):
            return se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-11-15", auto_merge=False)


def ok_repair(**kw):
    return {"ran": True, "ok": True, "post": dict(REPAIRED, slug="model-changed-it"), "skipped": None,
            "violations": [], "changes": [dict(CHANGE)], "targets": 1, **kw}


FAIL1 = _g(False, ["v1"], [{"uid": "S5", "rule": "claim"}])
FAIL2 = _g(False, ["claim is not supported by its source: \"Every dealer\""], [{"uid": "S5", "rule": "claim"}])


def test_repair_runs_once_after_the_failed_revision_then_the_final_gate_and_a_pass_publishes():
    e = Eng([dict(FAIL1), dict(FAIL2), _g(True)], ok_repair())
    out = e.run()
    assert e.order == ["assemble", "gate", "revise", "assemble", "gate", "repair", "assemble", "gate", "publish"]
    assert out["ok"] is True and out["pr_url"] == "https://gh/pull/1"
    post, gate_result, kw = e.repair_args
    assert gate_result["violations"] == FAIL2["violations"] and "pack" in kw
    assert e.gate_posts[2]["body_mdx"] == REPAIRED["body_mdx"], "the final gate judges the REPAIRED post"
    assert e.gate_posts[2]["slug"] == "inventory", "slug is pinned through the repair"
    assert e.files["src/content/blog/inventory.mdx"] == f"MDX:{REPAIRED['body_mdx']}", "the repaired post is what is committed"
    f = out["factcheck"]
    assert f["passed"] is True and f["passes"] == 3 and f["revised"] is True
    assert f["repair"] == {"ran": True, "applied": True, "skipped": None, "targets": 1,
                           "contract_violations": [], "changes": [CHANGE], "final_gate_passed": True}


def test_the_final_gate_reuses_the_link_cache_and_the_verdict_cache_and_runs_the_full_gate():
    e = Eng([dict(FAIL1), dict(FAIL2), _g(True)], ok_repair())
    e.run()
    k1, k2, k3 = e.gate_kw
    assert k1["link_cache"] is k2["link_cache"] is k3["link_cache"]
    assert k1["verdict_cache"] is k2["verdict_cache"] is k3["verdict_cache"]
    assert k2.get("skip_llm_on_deterministic_fail") is True
    assert "skip_llm_on_deterministic_fail" not in k3, "the last pass is the whole gate"


def test_final_gate_failure_holds_with_its_reasons_and_never_loops():
    e = Eng([dict(FAIL1), dict(FAIL2), _g(False, ["editor: operator_voice FAIL"]), _g(True)], ok_repair())
    out = e.run()
    assert e.order.count("gate") == 3 and e.order.count("repair") == 1 and "publish" not in e.order
    assert out["held"] is True and out["violations"] == ["editor: operator_voice FAIL"]
    assert out["factcheck"]["repair"]["final_gate_passed"] is False and out["factcheck"]["passes"] == 3


def test_a_repair_the_contract_rejects_holds_on_the_original_violations():
    rejected = {"ran": True, "ok": False, "post": POST, "skipped": None, "changes": [], "targets": 1,
                "violations": ['repair changed a sentence that had passed (body): "Start with the systems"']}
    e = Eng([dict(FAIL1), dict(FAIL2)], rejected)
    out = e.run()
    assert e.order == ["assemble", "gate", "revise", "assemble", "gate", "repair"], "no third gate pass is paid for"
    assert out["held"] is True and out["violations"] == FAIL2["violations"] + [
        'repair pass rejected, original violations stand: repair changed a sentence that had passed (body): '
        '"Start with the systems"']
    assert out["factcheck"]["repair"]["applied"] is False


def test_a_skipped_repair_holds_exactly_as_before_the_pass_existed():
    skipped = {"ran": False, "ok": False, "post": POST, "skipped": "9 failing sentences against 12 judged claims",
               "violations": [], "changes": [], "targets": 0}
    e = Eng([dict(FAIL1), dict(FAIL2)], skipped)
    out = e.run()
    assert e.order.count("gate") == 2 and out["violations"] == FAIL2["violations"]
    assert out["factcheck"]["repair"]["skipped"].startswith("9 failing sentences")


def test_repaired_post_that_fails_the_format_gate_holds():
    e = Eng([dict(FAIL1), dict(FAIL2), _g(True)], ok_repair(), fmt=[[], [], ["body is missing a PullQuote"]])
    out = e.run()
    assert e.order.count("gate") == 2 and "publish" not in e.order
    assert out["violations"] == ["repaired post failed the format gate", "body is missing a PullQuote"]


def test_repair_pass_false_in_config_holds_straight_after_the_revision():
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], repair_pass=False))
    e = Eng([dict(FAIL1), dict(FAIL2)], ok_repair(), cfg=cfg)
    out = e.run()
    assert "repair" not in e.order and out["violations"] == FAIL2["violations"] and "repair" not in out["factcheck"]


def test_a_gate_error_on_the_second_pass_is_never_repaired():
    err = dict(_g(False, ["factcheck gate error (post HELD, never published unchecked): boom"]), error="boom")
    e = Eng([dict(FAIL1), err], ok_repair())
    out = e.run()
    assert "repair" not in e.order and out["held"] is True


def test_a_post_that_passes_first_time_or_after_revision_never_reaches_repair():
    first = Eng([_g(True)], ok_repair())
    first.run()
    second = Eng([dict(FAIL1), _g(True)], ok_repair())
    second.run()
    assert "repair" not in first.order + second.order


def test_pr_body_shows_what_the_repair_pass_rewrote_and_deleted():
    changes = [dict(CHANGE), {"action": "rewritten", "where": "faq answer", "before": "It takes two weeks.",
                              "after": "The page gives no timeline.", "rules": ["editor:no_invented_timelines"]}]
    e = Eng([dict(FAIL1), dict(FAIL2), _g(True)], ok_repair(changes=changes))
    e.run()
    body = e.pr_body
    assert "### Repair pass" in body and body.index("### Fact-check gate") < body.index("### Repair pass")
    assert "2 sentence(s) were repaired under a code-enforced contract (1 rewritten, 1 deleted)" in body
    assert "| deleted | body | Every dealer in the country has already been fined \\| for missing one. | (deleted) | claim |" in body
    assert ("| rewritten | faq answer | It takes two weeks. | The page gives no timeline. | "
            "editor:no_invented_timelines |") in body
    assert EM_DASH not in body
    assert rp.pr_section({"applied": False, "changes": changes}) == "" and rp.pr_section(None) == ""


def test_real_repair_and_real_gate_end_to_end_through_run_brand(monkeypatch):
    """Un-stub everything but the transport: the real gate holds the post twice,
    the real repair deletes the two failing sentences under the real contract,
    and the real final gate passes on the carried verdicts."""
    verifier_calls = []

    def gate_llm(system, user):
        if system == fc.EDITOR_SYSTEM:
            bad = [ln.split(" ", 1)[0] for ln in user.splitlines() if "The truth is that this matters" in ln]
            return editor_reply(**({"no_filler_or_hype": {"evidence": FILLER, "sentence_ids": bad}} if bad else {}))
        verifier_calls.append(user)
        claims = []
        for ln in user.splitlines():
            sid = ln.split(" ", 1)[0]
            if "Dealers must keep a written inventory" in ln and "LINKS" in ln:
                claims.append({"sentence_id": sid, "claim": "inventory", "kind": "external", "source": SRC,
                               "verdict": "SUPPORTED", "quote": QUOTE})
            elif "has already been fined" in ln:
                claims.append({"sentence_id": sid, "claim": "all fined", "kind": "external", "source": SRC,
                               "verdict": "NOT_SUPPORTED", "quote": ""})
        return {"claims": claims}

    def repair_llm(system, user):
        out = []
        for chunk in user.split("=== FIELD ")[1:]:
            fid = chunk.split(" ", 1)[0]
            raw = chunk.split("RAW TEXT:\n", 1)[1].split("\nFAILING SENTENCE", 1)[0]
            raw = raw.replace(" " + OVERREACH, "").replace(FILLER + " ", "")
            raw = raw.replace("Every dealer has already been fined for missing one. ", "")
            out.append({"id": fid, "text": raw})
        return {"fields": out}

    monkeypatch.setattr(fc, "default_http_get", http)
    monkeypatch.setattr(fc, "default_llm", lambda model: gate_llm)
    monkeypatch.setattr(rp, "default_llm", lambda: repair_llm)
    published = {}
    # a tiny fixture post: 3 failing sentences of 4 judged claims, so lift the limit
    cfg = dict(CFG, factcheck=dict(CFG["factcheck"], repair_max_fail_ratio=1.0))
    with mock.patch.object(se, "_brand_cfg", return_value=cfg), \
         mock.patch.object(se, "run_factcheck", fc.run_gate), \
         mock.patch.object(se, "repair_post", rp.repair_post), \
         mock.patch.object(se, "generate_post", return_value=dict(POST)), \
         mock.patch.object(se, "revise_post", side_effect=lambda *a, **k: dict(POST)), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_mdx", side_effect=lambda p, d: (p["body_mdx"], [])), \
         mock.patch.object(se, "publish_post",
                           side_effect=lambda repo, br, files, t, body, tok: published.update(files=files, body=body) or "https://gh/pull/9"):
        out = se.run_brand("autointelligence", topic="t", token="tok", date_str="2026-11-15", auto_merge=False)
    assert out["ok"] is True, out
    f = out["factcheck"]
    assert f["passes"] == 3 and f["repair"]["applied"] is True and f["repair"]["final_gate_passed"] is True
    assert sorted(c["before"][:12] for c in f["repair"]["changes"]) == ["Every dealer", "Every dealer", "The truth is"]
    body = published["files"]["src/content/blog/inventory.mdx"]
    assert "fined" not in body and "The truth is" not in body and "Start with the systems you already own." in body
    assert "### Repair pass" in published["body"]
    assert f["counts"]["claims_carried"] >= 1
    assert "Dealers must keep a written inventory" not in verifier_calls[-1], "carried sentences are not re-judged"


def test_opted_out_brand_never_reaches_the_repair_pass():
    se._load_cfg.cache_clear()
    post = {"title": "T", "description": "D", "slug": "s", "heroAlt": "h", "faq": [],
            "body": [{"type": "answer", "text": "a"}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"linkedin": "li"}}
    never = mock.Mock(side_effect=AssertionError("must not run for an opted-out brand"))
    with mock.patch.object(se, "repair_post", never), mock.patch.object(se, "run_factcheck", never), \
         mock.patch.object(se, "build_source_pack", never), \
         mock.patch.object(se, "generate_post", return_value=dict(post)), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_shopify_article", return_value=({"title": "T"}, [])), \
         mock.patch.object(se, "create_article_draft", return_value={"ok": True, "dry_run": True, "blog_id": 1, "store": "s"}):
        out = se.run_brand("paperandpurpose", topic="t", token="tok", dry_run=True)
    assert out["ok"] is True and "factcheck" not in out
    never.assert_not_called()


# ---------------------------------------------------------------------------
# the dry-run script prints the repair diff
# ---------------------------------------------------------------------------

def test_dry_run_script_prints_the_repair_diff(monkeypatch, capsys):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "slipstream_gate_check.py")
    spec = importlib.util.spec_from_file_location("slipstream_gate_check_repair", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    gates = [dict(FAIL1), dict(FAIL2), _g(True)]
    changes = [dict(CHANGE), {"action": "rewritten", "where": "faq answer", "before": "It takes two weeks.",
                              "after": "The page gives no timeline.", "rules": ["editor:no_invented_timelines"]}]
    forbidden = mock.Mock(side_effect=AssertionError("a dry run must not publish"))
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    monkeypatch.setattr(se, "_brand_cfg", lambda b: CFG)
    monkeypatch.setattr(se, "generate_post", lambda cfg, topic, pack=None: dict(POST))
    monkeypatch.setattr(se, "revise_post", lambda *a, **k: dict(POST))
    monkeypatch.setattr(se, "assemble_mdx", lambda p, d: ("MDX", []))
    monkeypatch.setattr(se, "run_factcheck", lambda post, cfg, **kw: gates.pop(0))
    monkeypatch.setattr(se, "repair_post", lambda post, gr, cfg, **kw: ok_repair(changes=changes))
    for name in ("_checkoff_topic", "merge_when_green", "_distribute_social"):
        monkeypatch.setattr(se, name, forbidden)
    code = script.main(["--write", "--brand", "autointelligence", "--topic", "t"])
    out = capsys.readouterr().out
    forbidden.assert_not_called()
    assert code == 0 and "VERDICT PASS" in out
    assert "REPAIR  ran=True applied=True targets=1 final_gate_passed=True" in out
    assert "DELETED [body] rules=claim\n    - Every dealer in the country" in out
    assert "REWRITTEN [faq answer] rules=editor:no_invented_timelines\n    - It takes two weeks.\n    + The page gives no timeline." in out
