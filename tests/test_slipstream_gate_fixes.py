"""Gate fixes from the 2026-11-15 research dry runs. Offline.

1. The editor's own "[link]" / "[where]" prompt markers no longer fail a pass.
2. Block formats: a sentence the verifier SUPPORTED against a listed source
   counts as cited for the editor. An unsupported one does not.
3. A pure navigation / call-to-action sentence is not a brand claim. A sentence
   that asserts a capability, price, result or number still is.
4. Block formats: the post's `links` block pages are available to the verifier
   as the brand's own pages.
"""
import pytest

from services import slipstream_factcheck as fc

DOMAIN = "worshipdigital.co"
GOOGLE = "https://support.google.com/business/answer/3038177"
GOOGLE_TEXT = ("Guidelines for representing your business on Google. The boundaries of your overall "
               "area should not extend farther than about 2 hours of driving time from where your "
               "business is based. " + "More guidance for owners follows here. " * 20)
SERVICES_TEXT = ("Worship Digital builds and manages Google Business Profiles for small businesses. "
                 + "We also design websites and run ads. " * 20)


class Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text, self.headers = status_code, text, {"content-type": "text/html"}


def http(url):
    pages = {GOOGLE: Resp(200, f"<p>{GOOGLE_TEXT}</p>"),
             f"https://{DOMAIN}/services": Resp(200, f"<p>{SERVICES_TEXT}</p>"),
             f"https://{DOMAIN}/quote": Resp(200, "<p>" + "Request a quote. " * 60 + "</p>")}
    return pages.get(url) or Resp(404, "nope")


SOURCED = ("Google's guidelines say a service area should not extend farther than about 2 hours of "
           "driving time from where the business is based.")
UNSOURCED = "A new profile takes about 3 weeks to start ranking."


def block_post(*extra):
    return {"title": "How do I set my service area?", "description": "A plain answer.", "slug": "s",
            "body": [{"type": "answer", "text": "Set the area you really serve."},
                     {"type": "p", "text": SOURCED}, *extra,
                     {"type": "links", "title": "Next", "items": [{"label": "Services", "href": "/services"},
                                                                  {"label": "Quote", "href": "/quote"}]},
                     {"type": "sources", "title": "Sources", "items": [{"label": "Google", "href": GOOGLE}]}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"linkedin": "Read the post."}}


CFG = {"brand_key": "worshipdigital", "domain": DOMAIN, "factcheck": {"banned": [], "figure_allowlist": []}}
QUOTE = ("The boundaries of your overall area should not extend farther than about 2 hours of driving "
         "time from where your business is based.")


def _uid(post, text):
    units, _ = fc.extract_post(post)
    return next(u.uid for u in units if u.text == text)


def editor(**fails):
    """All-pass editor reply, with the named items failed on the given evidence."""
    items = []
    for rid, _q, needs in fc.EDITOR_RUBRIC:
        if rid in fails:
            items.append({"id": rid, "pass": False, "evidence": fails[rid]})
        else:
            items.append({"id": rid, "pass": True,
                          "evidence": "Set the area you really serve." if needs else ""})
    return {"items": items}


def gate(post, claims, editor_reply=None, capture=None):
    def llm(system, user):
        if capture is not None:
            capture.append((system, user))
        return {"claims": claims} if system == fc.VERIFIER_SYSTEM else (editor_reply or editor())
    return fc.run_gate(post, CFG, http_get=http, llm=llm, facts_text="")


# ---------------------------------------------------------------- 1. markers

@pytest.mark.parametrize("copied", [
    "Set the area you really serve. [link]",
    "[answer] Set the area you really serve.",
    "[answer] Set the area you really serve. [link]",
    "Set the area you really serve.",
])
def test_editor_evidence_copied_with_the_prompts_own_markers_still_counts(copied):
    post = block_post()
    units, _ = fc.extract_post(post)
    reply = editor()
    for item in reply["items"]:
        if item["id"] in ("answers_title", "concrete_specific"):
            item["evidence"] = copied
    violations, records = fc.evaluate_editor(reply, units)
    assert violations == [] and all(r["pass"] for r in records)


def test_stripping_markers_does_not_let_invented_evidence_through():
    units, _ = fc.extract_post(block_post())
    reply = editor()
    reply["items"][1]["evidence"] = "[stat] 93 percent of owners never check this [link]"
    violations, _ = fc.evaluate_editor(reply, units)
    assert len(violations) == 1 and "evidence that is not in the post" in violations[0]
    assert fc.strip_prompt_markers("A sentence about [link] building.") == "A sentence about [link] building."


# ------------------------------------------- 2. block formats and citations

def test_block_format_sentence_supported_by_a_listed_source_is_marked_cited_for_the_editor():
    post = block_post()
    seen = []
    claims = [{"sentence_id": _uid(post, SOURCED), "claim": "2 hours of driving time", "kind": "external",
               "source": GOOGLE, "verdict": "SUPPORTED", "quote": QUOTE}]
    r = gate(post, claims, capture=seen)
    assert r["ok"] is True, r["violations"]
    editor_prompt = seen[1][1]
    assert f"[p] {SOURCED} [link]" in editor_prompt
    assert "this format cannot put a link inside a sentence" in editor_prompt
    assert "[answer] Set the area you really serve.\n" in editor_prompt + "\n", "an unjudged line gets no marker"


def test_block_format_unsupported_timeline_is_still_caught_twice():
    """The sentence has no source. The verifier cannot support it, so it gets
    no [link] marker, the editor fails it, and the verifier violation stands."""
    post = block_post({"type": "p", "text": UNSOURCED})
    seen = []
    claims = [{"sentence_id": _uid(post, SOURCED), "claim": "2 hours", "kind": "external",
               "source": GOOGLE, "verdict": "SUPPORTED", "quote": QUOTE},
              {"sentence_id": _uid(post, UNSOURCED), "claim": "3 weeks to rank", "kind": "external",
               "source": None, "verdict": "UNVERIFIABLE", "quote": ""}]
    r = gate(post, claims, editor(no_invented_timelines=UNSOURCED), capture=seen)
    assert f"[p] {UNSOURCED}\n" in seen[1][1] + "\n" and f"{UNSOURCED} [link]" not in seen[1][1]
    assert r["ok"] is False
    assert any("no_invented_timelines FAIL" in v for v in r["violations"])
    assert any(v.startswith("unverifiable claim") and "3 weeks" in v for v in r["violations"])
    assert any('figure "3 weeks"' in v or 'figure "3"' in v for v in r["violations"]), "the code check holds it too"


def test_a_supported_verdict_with_a_made_up_quote_does_not_mark_the_sentence_cited():
    post = block_post({"type": "p", "text": UNSOURCED})
    seen = []
    claims = [{"sentence_id": _uid(post, UNSOURCED), "claim": "3 weeks to rank", "kind": "external",
               "source": GOOGLE, "verdict": "SUPPORTED", "quote": "A new profile takes about 3 weeks to rank."}]
    r = gate(post, claims, capture=seen)
    assert f"{UNSOURCED} [link]" not in seen[1][1]
    assert any("quoted passage is not in the source text" in v for v in r["violations"])


def test_a_sentence_with_one_supported_and_one_unsupported_claim_is_not_cited():
    ids = fc.sourced_sentence_ids([
        {"sentence_id": "S1", "verdict": "SUPPORTED", "source": GOOGLE},
        {"sentence_id": "S2", "verdict": "SUPPORTED", "source": GOOGLE},
        {"sentence_id": "S2", "verdict": "UNVERIFIABLE", "source": ""},
        {"sentence_id": "S3", "verdict": "NOT_A_CLAIM", "source": "/quote"},
        {"sentence_id": "S4", "verdict": "SUPPORTED", "source": ""}])
    assert ids == {"S1"}


def test_mdx_posts_keep_the_old_editor_prompt():
    post = {"title": "T", "description": "D", "slug": "s",
            "body_mdx": f"<AnswerFirst>Set the area you really serve.</AnswerFirst>\n\nSee [the rule]({GOOGLE}).",
            "social": {}}
    units, _ = fc.extract_post(post)
    prompt = fc.build_editor_prompt(post, units, "")
    assert "POST (one sentence per line; [link] marks a sentence that carries a source link):" in prompt
    assert "this format cannot" not in prompt


# ------------------------------------------------- 3. navigation sentences

NAV = [
    "Book a discovery call to talk it through.",
    "From there, a discovery call is the place to ask about timing for your specific business.",
    "Write it down, and bring it to a discovery call for specifics on your situation.",
    "If you have questions first, reach out through the contact page.",
    "See the services page.",
]
CLAIMS = [
    "We answer every call in under three rings.",                                   # capability + number
    "Book a discovery call and we will have you live in 48 hours.",                 # timeline
    "Request a quote, it is free and there is no contract.",                        # price / guarantee
    "Our clients get more leads, so book a call.",                                  # result
    "The how it works page lays out how the stages fit together.",                  # asserts page content
    "The service books jobs straight into your calendar, so reach out.",            # capability
    "Plans start at $197 per month, see the pricing page.",                         # price
    "A recurring version of this kind of review is outlined under services.",       # asserts an offer
]


@pytest.mark.parametrize("text", NAV)
def test_pure_navigation_sentences_are_not_brand_claims(text):
    unit = fc.Unit(text=text, links=["/discovery"], uid="S1")
    assert fc.is_navigation_sentence(unit, "theaiphoneguy.com") is True
    for verdict in ("NOT_SUPPORTED", "UNVERIFIABLE"):
        v, records = fc.evaluate_verifier(
            {"claims": [{"sentence_id": "S1", "claim": "c", "kind": "brand", "source": "/discovery",
                         "verdict": verdict, "quote": ""}]}, [unit], {}, "", "theaiphoneguy.com")
        assert v == [] and records[0]["verdict"] == "NOT_A_CLAIM"


@pytest.mark.parametrize("text", CLAIMS)
def test_a_sentence_that_asserts_something_about_the_brand_is_still_judged_and_fails(text):
    unit = fc.Unit(text=text, links=["/discovery"], uid="S1")
    assert fc.is_navigation_sentence(unit, "theaiphoneguy.com") is False
    v, records = fc.evaluate_verifier(
        {"claims": [{"sentence_id": "S1", "claim": "c", "kind": "brand", "source": "/discovery",
                     "verdict": "UNVERIFIABLE", "quote": ""}]}, [unit], {}, "", "theaiphoneguy.com")
    assert len(v) == 1 and v[0].startswith("unverifiable claim") and records[0]["verdict"] == "UNVERIFIABLE"


def test_navigation_waiver_is_narrow():
    nav = "Book a discovery call to talk it through."
    assert fc.is_navigation_sentence(fc.Unit(text=nav, links=["https://other.example/x"]), "theaiphoneguy.com") is False
    assert fc.is_navigation_sentence(fc.Unit(text=nav, links=[]), "theaiphoneguy.com") is True
    assert fc.is_navigation_sentence(fc.Unit(text="Dashboards show averages."), "x.com") is False
    assert fc.is_navigation_sentence(fc.Unit(text=nav + " " + "word " * 50), "x.com") is False
    assert fc.is_navigation_sentence(None) is False
    # a SUPPORTED verdict whose quote code cannot find still fails, navigation or not
    unit = fc.Unit(text=nav, links=["/discovery"], uid="S1")
    v, records = fc.evaluate_verifier(
        {"claims": [{"sentence_id": "S1", "claim": "c", "kind": "brand", "source": "facts",
                     "verdict": "SUPPORTED", "quote": "We offer a discovery call to every owner."}]},
        [unit], {}, "", "theaiphoneguy.com")
    assert len(v) == 1 and records[0]["verdict"] == "QUOTE_NOT_IN_SOURCE"


def test_not_a_claim_is_recorded_and_not_counted_against_the_post():
    post = block_post({"type": "p", "text": "Request a quote to talk it through."})
    claims = [{"sentence_id": _uid(post, "Request a quote to talk it through."), "claim": "offers quotes",
               "kind": "brand", "source": "/quote", "verdict": "NOT_SUPPORTED", "quote": ""}]
    r = gate(post, claims)
    assert r["ok"] is True, r["violations"]
    assert r["claims"][0]["verdict"] == "NOT_A_CLAIM" and r["counts"]["claims_not_supported"] == 0


def test_banned_content_still_fires_on_a_navigation_sentence():
    cfg = dict(CFG, factcheck={"banned": [{"id": "aipg_call_transfer_not_live", "label": "transfer",
                                           "patterns": [r"\btransfer(?:s|red|ring)?\b"]}]})
    post = block_post({"type": "p", "text": "Book a call to ask about call transfer."})
    r = fc.run_gate(post, cfg, http_get=http, facts_text="",
                    llm=lambda s, u: {"claims": []} if s == fc.VERIFIER_SYSTEM else editor())
    assert r["ok"] is False and any("banned content [aipg_call_transfer_not_live]" in v for v in r["violations"])


# ------------------------------------------- 4. brand pages in block formats

def test_block_format_brand_claim_is_supported_by_a_page_in_the_links_block():
    sentence = "Worship Digital builds and manages Google Business Profiles for small businesses."
    post = block_post({"type": "p", "text": sentence})
    seen = []
    claims = [{"sentence_id": _uid(post, sentence), "claim": "builds and manages profiles", "kind": "brand",
               "source": "/services", "verdict": "SUPPORTED", "quote": sentence}]
    r = gate(post, claims, capture=seen)
    assert "POST-LEVEL BRAND PAGES: /services, /quote" in seen[0][1]
    assert "=== SOURCE /services ===" in seen[0][1]
    assert r["ok"] is True, r["violations"]


def test_block_format_brand_claim_the_page_does_not_state_still_fails():
    sentence = "Worship Digital guarantees a top three ranking."
    post = block_post({"type": "p", "text": sentence})
    claims = [{"sentence_id": _uid(post, sentence), "claim": "guarantees ranking", "kind": "brand",
               "source": "/services", "verdict": "SUPPORTED", "quote": sentence}]
    r = gate(post, claims)
    assert r["ok"] is False and any("quoted passage is not in the source text" in v for v in r["violations"])
