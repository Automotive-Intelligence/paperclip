"""Offline tests for the Slipstream fact-check + editor gate.

Fake HTTP and a fake LLM only: no network, no model, no image generation. The
fixtures are modeled on the real failures in the 2026-11-12 audit
(avo-telemetry/reports/blog-factcheck-2026-11-12.md): the dead CDK link, a
number that is not in its source, the "Michael here" anecdote, the AI Phone Guy
"transfer" claim, and a quoted passage the verifier made up.
"""
import pytest

from services import slipstream_engine as se
from services import slipstream_factcheck as fc

EM_DASH = chr(0x2014)


# ------------------------------------------------------------------ fakes

class Resp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "text/html"}


def fake_http(pages, default=None):
    """pages: {url: Resp | Exception | [Resp, ...]}. Records every call."""
    calls = []

    def _get(url):
        calls.append(url)
        v = pages.get(url, default)
        if v is None:
            raise AssertionError(f"unexpected fetch: {url}")
        if isinstance(v, list):
            v = v.pop(0) if len(v) > 1 else v[0]
        if isinstance(v, Exception):
            raise v
        return v
    _get.calls = calls
    return _get


def editor_ok(first_line, specific_line):
    return {"items": [
        {"id": "answers_title", "pass": True, "evidence": first_line},
        {"id": "concrete_specific", "pass": True, "evidence": specific_line},
        {"id": "no_filler_or_hype", "pass": True, "evidence": ""},
        {"id": "no_unsupported_absolutes", "pass": True, "evidence": ""},
        {"id": "no_invented_timelines", "pass": True, "evidence": ""},
        {"id": "no_em_dashes", "pass": True, "evidence": ""},
        {"id": "operator_voice", "pass": True, "evidence": ""},
    ]}


def fake_llm(verifier_reply, editor_reply):
    calls = []

    def _llm(system, user):
        calls.append((system, user))
        reply = verifier_reply if system == fc.VERIFIER_SYSTEM else editor_reply
        if isinstance(reply, Exception):
            raise reply
        return reply
    _llm.calls = calls
    return _llm


FTC = "https://www.ftc.gov/safeguards"
FTC_TEXT = ("<html><body><h1>Safeguards Rule</h1><p>Conduct a periodic inventory of data, noting "
            "where it's collected, stored, or transmitted. The rule covers 15,000 dealers and "
            "54 percent of them finance vehicles.</p><script>var x = 99;</script></body></html>")
AVI_CFG = {"brand_key": "autointelligence", "domain": "automotiveintelligence.io", "factcheck": {}}

CLEAN_MDX = (
    "<AnswerFirst>No single dealership system owns the customer record by default.</AnswerFirst>\n\n"
    "## Who has to inventory the data?\n\n"
    f"The FTC tells covered businesses to keep a periodic inventory of customer data ([FTC guidance]({FTC})).\n\n"
    'Start with a <a href="/diagnostic-call">diagnostic call</a>.\n\n'
    '<img src="/blog/x-hero.png" alt="Three data streams converging"/>\n'
)
CLEAN_POST = {"title": "Who Owns the Customer Record?", "description": "A field by field answer.",
              "slug": "x", "body_mdx": CLEAN_MDX, "image_prompts": [{"name": "hero", "prompt": "p"}],
              "social": {"linkedin": "Who owns the record? Nobody, by default."}}
CLEAN_PAGES = {FTC: Resp(200, FTC_TEXT),
               "https://automotiveintelligence.io/diagnostic-call": Resp(200, "<p>30-min diagnostic call</p>")}
CLEAN_VERIFIER = {"claims": [{
    "sentence_id": "S5", "claim": "FTC tells covered businesses to inventory customer data",
    "kind": "external", "source": FTC, "verdict": "SUPPORTED",
    "quote": "Conduct a periodic inventory of data, noting where it's collected, stored, or transmitted."}]}
CLEAN_EDITOR = editor_ok("No single dealership system owns the customer record by default.",
                         "The FTC tells covered businesses to keep a periodic inventory of customer data")


def run_clean(**kw):
    args = dict(http_get=fake_http(dict(CLEAN_PAGES)), llm=fake_llm(CLEAN_VERIFIER, CLEAN_EDITOR),
                facts_text="", model="fake-verifier")
    args.update(kw)
    return fc.run_gate(kw.pop("post", CLEAN_POST), AVI_CFG, **{k: v for k, v in args.items() if k != "post"})


# ------------------------------------------------------------------ extraction (both shapes)

def test_mdx_sentences_keep_their_own_links_in_all_three_link_forms():
    body = ('The attack took systems offline (<a href="https://a.example/cdk">CDK updates</a>). '
            "Separately, a rule applies ([FTC](https://b.example/ftc)). "
            "A third thing is worth a skim (https://c.example/x). No link here.")
    units = fc.units_from_mdx(body)
    assert [u.links for u in units] == [["https://a.example/cdk"], ["https://b.example/ftc"],
                                        ["https://c.example/x"], []]
    assert units[0].text.startswith("The attack took systems offline")
    assert "https://" not in " ".join(u.text for u in units)


def test_mdx_alt_text_table_rows_and_statrow_are_extracted():
    body = ('<img src="/blog/a-hero.png" alt="human transfer paths"/>\n'
            "| Field | Source |\n|---|---|\n| Phone | CRM, updated by 12 reps |\n"
            '<StatRow value="54%" label="finance vehicles" source="FTC, 2025" href="https://b.example/ftc" />\n')
    units = fc.units_from_mdx(body)
    alt = [u for u in units if u.where == "image alt"][0]
    assert alt.surface == "meta" and "transfer" in alt.text
    assert any(u.where == "table row" and "12 reps" in u.text for u in units)
    stat = [u for u in units if u.where == "StatRow"][0]
    assert stat.links == ["https://b.example/ftc"] and "54%" in stat.text
    # an image src is never treated as a link to check
    assert all("/blog/a-hero.png" not in u.links for u in units)


WD_POST = {
    "title": "How Do I Optimize My Google Business Profile?", "description": "A walkthrough.",
    "slug": "gbp", "heroAlt": "A storefront at dusk",
    "faq": [{"q": "How often should I post?", "a": "Once or twice a week is plenty."}],
    "body": [
        {"type": "answer", "text": "Fill out every section completely and respond to every review."},
        {"type": "p", "text": "Michael here. I've sat across the table from enough small business owners "
                              "to know this conversation always starts the same way."},
        {"type": "definition", "term": "Google Business Profile", "text": "The free listing on Google."},
        {"type": "ul", "items": ["Accurate hours", "A phone number that rings to a real person"]},
        {"type": "table", "headers": ["Area", "Win"], "rows": [["Reviews", "Reply to every one"]]},
        {"type": "image", "src": "/blog/gbp-a.png", "alt": "A map pin", "caption": "Photos show activity."},
        {"type": "links", "title": "Want help?", "items": [{"label": "Get a quote", "href": "/quote"}]},
        {"type": "sources", "title": "Further reading", "items": [
            {"label": "Google guide", "href": "https://support.google.com/business/answer/9095494"}]},
    ],
    "image_prompts": [{"name": "hero", "prompt": "p"}],
    "social": {"x": "Your profile is not set and forget."},
}
WD_CFG = {"brand_key": "worshipdigital", "domain": "worshipdigital.co"}


def test_block_shape_yields_text_links_sources_and_secondary_surfaces():
    units, post_sources = fc.extract_post(WD_POST)
    assert post_sources == ["https://support.google.com/business/answer/9095494"]
    wheres = {u.where for u in units}
    assert {"answer", "p", "definition", "list item", "table row", "image caption",
            "links block", "sources block", "faq answer", "social x", "title"} <= wheres
    assert any(u.links == ["/quote"] for u in units)
    assert [u.surface for u in units if u.where == "faq answer"] == ["secondary"]
    assert all(u.uid for u in units) and len({u.uid for u in units}) == len(units)


def test_post_with_neither_body_shape_holds_instead_of_passing():
    r = fc.run_gate({"title": "t"}, AVI_CFG, http_get=fake_http({}), llm=fake_llm({}, {}), facts_text="")
    assert r["ok"] is False and r["error"] and "HELD" in r["violations"][0]


# ------------------------------------------------------------------ links

def test_link_states_live_dead_and_could_not_check():
    root = {"https://x.example/": Resp(200, "home")}
    assert fc.check_link("https://x.example/a", fake_http({"https://x.example/a": Resp(200, "<p>hi</p>")})).state == "live"
    assert fc.check_link("https://x.example/a", fake_http({**root, "https://x.example/a": Resp(404)})).state == "dead"
    assert fc.check_link("https://x.example/a", fake_http({**root, "https://x.example/a": Resp(410)})).state == "dead"
    for code in (401, 403, 429, 500, 503):
        f = fc.check_link("https://x.example/a", fake_http({"https://x.example/a": Resp(code)}))
        assert f.state == "unchecked" and f.status == code
    assert fc.check_link("https://x.example/a", fake_http({"https://x.example/a": TimeoutError("slow")})).state == "unchecked"
    f = fc.check_link("https://gone.example/a", fake_http({"https://gone.example/a": fc.DnsFailure("nodename")}))
    assert f.state == "dead" and "DNS" in f.detail


def test_link_is_retried_once_before_a_verdict():
    http = fake_http({"https://x.example/a": [Resp(503), Resp(200, "<p>fine now</p>")]})
    f = fc.check_link("https://x.example/a", http)
    assert f.state == "live" and "fine now" in f.text and len(http.calls) == 2


def test_a_404_from_a_host_whose_root_also_fails_is_could_not_check_not_dead():
    """ftc.gov answered every automated request with 404 in the audit, its own
    home page included. That is a bot block, not a dead page."""
    http = fake_http({"https://www.ftc.gov/robocalls": Resp(404), "https://www.ftc.gov/": Resp(404)})
    f = fc.check_link("https://www.ftc.gov/robocalls", http)
    assert f.state == "unchecked" and "blocking automated checks" in f.detail


def test_page_text_drops_scripts_and_tags():
    f = fc.check_link(FTC, fake_http({FTC: Resp(200, FTC_TEXT)}))
    assert "periodic inventory" in f.text and "var x" not in f.text and "<p>" not in f.text


def test_audit_fixture_dead_cdk_link_is_a_violation():
    cdk = "https://www.cdkglobal.com/cyber-incident"
    post = dict(CLEAN_POST, body_mdx=CLEAN_MDX + "\nThe CDK Global cyberattack took core DMS functions offline "
                f'(<a href="{cdk}">CDK Global incident updates</a>).\n')
    http = fake_http({**CLEAN_PAGES, cdk: Resp(404), "https://www.cdkglobal.com/": Resp(200, "home")})
    r = fc.run_gate(post, AVI_CFG, http_get=http, use_llm=False, facts_text="")
    assert r["ok"] is False
    assert any(v.startswith("dead source link") and cdk in v for v in r["violations"])
    assert r["counts"]["links_dead"] == 1


def test_could_not_check_is_a_violation_too():
    post = dict(CLEAN_POST)
    http = fake_http({**CLEAN_PAGES, FTC: Resp(403)})
    r = fc.run_gate(post, AVI_CFG, http_get=http, use_llm=False, facts_text="")
    assert any(v.startswith("could not check source link") and FTC in v for v in r["violations"])
    assert r["counts"]["links_unchecked"] == 1 and r["error"] is None


def test_internal_links_resolve_on_the_brand_domain_and_must_be_200():
    http = fake_http({FTC: Resp(200, FTC_TEXT),
                      "https://automotiveintelligence.io/diagnostic-call": Resp(404),
                      "https://automotiveintelligence.io/": Resp(200, "home")})
    r = fc.run_gate(CLEAN_POST, AVI_CFG, http_get=http, use_llm=False, facts_text="")
    assert any(v.startswith("internal link does not return 200 on automotiveintelligence.io")
               and "/diagnostic-call" in v for v in r["violations"])
    assert "https://automotiveintelligence.io/diagnostic-call" in http.calls
    assert fc.resolve_link("https://www.automotiveintelligence.io/services", "automotiveintelligence.io")[1] is True
    assert fc.resolve_link("mailto:a@b.co", "x.io") is None and fc.resolve_link("#top", "x.io") is None


def test_every_link_is_fetched_once_and_the_cache_is_reused_across_passes():
    cache = {}
    http = fake_http(dict(CLEAN_PAGES))
    fc.run_gate(CLEAN_POST, AVI_CFG, http_get=http, use_llm=False, facts_text="", link_cache=cache)
    n = len(http.calls)
    fc.run_gate(CLEAN_POST, AVI_CFG, http_get=http, use_llm=False, facts_text="", link_cache=cache)
    assert n == 2 and len(http.calls) == n


# ------------------------------------------------------------------ figures

@pytest.mark.parametrize("in_post,in_source", [
    ("54%", "54 percent"), ("54 percent", "54%"), ("1,400", "1400"), ("$5K", "$5,000"),
    ("$5,000", "$ 5000"), ("1.4M", "1.4 million"), ("15,000", "15,000+ locations"),
    ("88.46 percent", "88.46%"), ("two weeks", "nearly two weeks later"), ("2", "two"),
])
def test_figure_normalization_matches_the_same_figure_written_differently(in_post, in_source):
    figs = fc.extract_figures(f"About {in_post} of it.")
    assert figs, in_post
    assert all(fc.figure_in(f, fc.index_text(f"The report found {in_source} overall.")) for f in figs)


@pytest.mark.parametrize("in_post,in_source", [
    ("54%", "45 percent"), ("5%", "5 dealers"), ("$5K", "$50,000"), ("1,400", "14,000"),
    ("one to three business days", "three business days"),
])
def test_a_different_figure_never_matches(in_post, in_source):
    figs = fc.extract_figures(f"About {in_post} of it.")
    assert figs and not all(fc.figure_in(f, fc.index_text(f"The report found {in_source}.")) for f in figs)


def test_years_and_allowlisted_tokens_are_not_figures_but_spelled_out_quantities_are():
    assert fc.extract_figures("The 2024 CDK attack. We answer 24/7.", ["24/7"]) == []
    assert [f.raw for f in fc.extract_figures("live in one to three business days")] == ["one to three business days"]
    assert [f.raw for f in fc.extract_figures("cut the timeline roughly in half")] == ["in half"]
    assert fc.extract_figures("One day you will thank yourself, and it stops double-booking a tech.") == []


def _fig_post(sentence):
    return dict(CLEAN_POST, body_mdx=CLEAN_MDX + "\n" + sentence + "\n")


def test_audit_fixture_number_not_present_in_its_source_is_a_violation():
    """A real link that does not say what the post claims: the page loads, the
    sentence cites it, and the number is simply not on it."""
    r = fc.run_gate(_fig_post(f"Roughly 88% of dealers finance vehicles ([FTC]({FTC}))."),
                    AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False, facts_text="")
    assert any('figure "88%" does not appear in the cited source' in v and FTC in v for v in r["violations"])
    assert r["counts"]["figures_unmatched"] == 1


def test_figure_present_in_its_source_passes():
    r = fc.run_gate(_fig_post(f"About 54% of the 15,000 dealers finance vehicles ([FTC]({FTC}))."),
                    AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False, facts_text="")
    assert r["violations"] == [] and r["counts"]["figures_checked"] == 2


def test_figure_with_no_source_link_in_its_sentence_is_a_violation():
    r = fc.run_gate(_fig_post("Most setups go live in one to three business days."),
                    AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False, facts_text="")
    assert any('figure "one to three business days" has no source link in its sentence' in v
               for v in r["violations"])


def test_a_link_in_the_next_sentence_does_not_cover_the_figure():
    r = fc.run_gate(_fig_post(f"About 54% of dealers finance vehicles. See the rule ([FTC]({FTC}))."),
                    AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False, facts_text="")
    assert any('figure "54%" has no source link' in v for v in r["violations"])


def test_figure_from_the_brand_facts_file_needs_no_link():
    r = fc.run_gate(_fig_post("The diagnostic call is 30 minutes and Phase 1 starts at $5,000."),
                    AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False,
                    facts_text="The diagnostic call is 30 minutes.\nPhase 1 starts at $5,000.")
    assert r["violations"] == []


def test_figure_whose_source_did_not_load_cannot_be_confirmed():
    http = fake_http({**CLEAN_PAGES, FTC: Resp(403)})
    r = fc.run_gate(_fig_post(f"About 54% of dealers finance vehicles ([FTC]({FTC}))."),
                    AVI_CFG, http_get=http, use_llm=False, facts_text="")
    assert any('figure "54%" cannot be confirmed' in v for v in r["violations"])


def test_block_shape_figures_fall_back_to_the_posts_sources_block():
    src = "https://support.google.com/business/answer/7091"
    post = dict(WD_POST, faq=[], body=[
        {"type": "answer", "text": "Complete profiles get 70% more visits."},
        {"type": "p", "text": "And 12 photos is the floor."},
        {"type": "sources", "title": "s", "items": [{"label": "Google", "href": src}]}])
    http = fake_http({src: Resp(200, "<p>Complete profiles get 70% more visits.</p>")})
    r = fc.run_gate(post, WD_CFG, http_get=http, use_llm=False, facts_text="")
    assert len(r["violations"]) == 1 and 'figure "12"' in r["violations"][0]


def test_social_and_faq_figures_must_repeat_a_verified_body_figure():
    post = dict(_fig_post(f"About 54% of dealers finance vehicles ([FTC]({FTC}))."),
                social={"x": "54% of dealers finance. And 9 in 10 skip the audit."})
    r = fc.run_gate(post, AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False, facts_text="")
    assert len(r["violations"]) == 2
    assert all("in the social x is not a sourced, verified figure" in v for v in r["violations"])
    assert not any('"54%"' in v for v in r["violations"])


# ------------------------------------------------------------------ banned content (real config)

def _real(brand):
    return se._brand_cfg(brand)


def test_audit_fixture_michael_here_anecdote_is_banned_for_every_brand():
    r = fc.run_gate(WD_POST, _real("worshipdigital"), use_llm=False, facts_text="",
                    http_get=fake_http({}, default=Resp(200, "ok")))
    banned = [v for v in r["violations"] if "[first_person_experience]" in v]
    assert any("Michael here" in v for v in banned)
    assert any("I've sat across the table" in v for v in banned)
    for brand in ("autointelligence", "aiphoneguy", "worshipdigital", "paperandpurpose"):
        ids = [b["id"] for b in _real(brand)["factcheck"]["banned"]]
        assert "first_person_experience" in ids, brand


@pytest.mark.parametrize("sentence", [
    "Michael here.", "I've sat across the desk from hundreds of buyers.",
    "In my store we fixed this in a month.", "We've seen this at every rooftop.",
    "Our clients usually cut it in half.", "In our experience the CRM wins.",
])
def test_first_person_experience_patterns(sentence):
    rules = _real("autointelligence")["factcheck"]["banned"]
    assert fc.check_banned([fc.Unit(text=sentence)], rules, "")


def test_first_person_sentence_is_allowed_only_when_it_is_in_the_facts_file():
    rules = _real("autointelligence")["factcheck"]["banned"]
    s = "I've sold cars on a showroom floor for years."
    assert fc.check_banned([fc.Unit(text=s)], rules, "") != []
    assert fc.check_banned([fc.Unit(text=s)], rules, "Some other approved line.") != []
    assert fc.check_banned([fc.Unit(text=s)], rules, f"- note\n{s}") == []


def test_plain_brand_voice_is_not_mistaken_for_an_anecdote():
    rules = _real("autointelligence")["factcheck"]["banned"]
    for s in ("Our services page lists the three phases.", "We start with a diagnostic call.",
              "I think the CRM should own contact info."):
        assert fc.check_banned([fc.Unit(text=s)], rules, "") == [], s


@pytest.mark.parametrize("sentence,rule", [
    ("The AI can either book an appointment or route the call to a human.", "aipg_call_transfer_not_live"),
    ("You and the vendor adjust the transfer rules.", "aipg_call_transfer_not_live"),
    ("Diagram of forwarding, AI answering, and human transfer paths", "aipg_call_transfer_not_live"),
    ("It offers call screening for spam.", "aipg_call_screening_not_live"),
    ("A missed-call text-back goes out in seconds.", "aipg_text_back_not_live"),
    ("It texts you the details after every call.", "aipg_text_back_not_live"),
    ("Vendors can connect to most scheduling software in a day or two.", "aipg_crm_scheduling_integration_not_live"),
    ("It syncs with your CRM automatically.", "aipg_crm_scheduling_integration_not_live"),
    ("It plugs into ServiceTitan.", "aipg_crm_scheduling_integration_not_live"),
])
def test_audit_fixture_aiphoneguy_capabilities_that_are_not_live(sentence, rule):
    out = fc.check_banned([fc.Unit(text=sentence)], _real("aiphoneguy")["factcheck"]["banned"], "")
    assert any(f"[{rule}]" in v for v in out), out


def test_aiphoneguy_live_behavior_is_not_flagged():
    rules = _real("aiphoneguy")["factcheck"]["banned"]
    for s in ("Most setups work through call forwarding rather than replacing your hardware.",
              "It books onto a calendar we set up for your business.",
              "The owner gets an email alert after each call.",
              "It answers, takes a message, and books the appointment."):
        assert fc.check_banned([fc.Unit(text=s)], rules, "") == [], s


def test_aiphoneguy_rules_do_not_leak_to_other_brands():
    ids = [b["id"] for b in _real("autointelligence")["factcheck"]["banned"]]
    assert not any(i.startswith("aipg_") for i in ids)


def test_worshipdigital_faith_framing_is_banned_but_the_brand_name_is_not():
    rules = _real("worshipdigital")["factcheck"]["banned"]
    assert fc.check_banned([fc.Unit(text="Worship Digital handles the upkeep for you.")], rules, "") == []
    for s in ("Great for churches and ministries.", "A faith-based approach to marketing.",
              "We pray over every campaign.", "Grow your congregation online."):
        assert any("[wd_no_faith_framing]" in v for v in fc.check_banned([fc.Unit(text=s)], rules, "")), s
    # Paper & Purpose IS a faith-informed brand: the WD rule must not apply to it.
    assert "wd_no_faith_framing" not in [b["id"] for b in _real("paperandpurpose")["factcheck"]["banned"]]


def test_a_broken_banned_pattern_holds_the_post():
    cfg = dict(AVI_CFG, factcheck={"banned": [{"id": "bad", "patterns": ["("]}]})
    r = fc.run_gate(CLEAN_POST, cfg, http_get=fake_http(dict(CLEAN_PAGES)), use_llm=False, facts_text="")
    assert r["ok"] is False and "invalid pattern" in r["error"]


# ------------------------------------------------------------------ verifier

def test_clean_post_passes_the_whole_gate():
    r = run_clean()
    assert r["ok"] is True and r["violations"] == [] and r["error"] is None
    assert r["model"] == "fake-verifier" and r["llm_ran"] is True
    assert r["counts"]["claims"] == 1 and r["counts"]["claims_supported"] == 1
    assert r["claims"][0]["verdict"] == "SUPPORTED" and r["claims"][0]["source"] == FTC


def test_verifier_prompt_carries_sentences_links_facts_and_fetched_source_text():
    llm = fake_llm(CLEAN_VERIFIER, CLEAN_EDITOR)
    run_clean(llm=llm, facts_text="The diagnostic call is 30 minutes.")
    system, user = llm.calls[0]
    assert system == fc.VERIFIER_SYSTEM
    assert "S5 [body]" in user and f"LINKS: {FTC}" in user
    assert "The diagnostic call is 30 minutes." in user
    assert f"=== SOURCE {FTC} ===" in user and "periodic inventory of data" in user
    assert len(llm.calls) == 2 and llm.calls[1][0] == fc.EDITOR_SYSTEM


def test_audit_fixture_a_quote_the_verifier_made_up_is_not_accepted():
    """The verifier says SUPPORTED and 'quotes' a passage that is not in the
    fetched source. Code must catch it; the model's word is not enough."""
    made_up = {"claims": [dict(CLEAN_VERIFIER["claims"][0],
                               quote="Dealers must know exactly where customer financial data lives.")]}
    r = run_clean(llm=fake_llm(made_up, CLEAN_EDITOR))
    assert r["ok"] is False
    assert any("quoted passage is not in the source text" in v and "periodic inventory" in v
               for v in r["violations"])
    assert r["claims"][0]["verdict"] == "QUOTE_NOT_IN_SOURCE"
    assert r["counts"]["claims_not_supported"] == 1


def test_quote_match_ignores_case_whitespace_and_curly_quotes_only():
    ok = {"claims": [dict(CLEAN_VERIFIER["claims"][0],
                          quote="conduct a   periodic inventory of data, noting where it’s collected")]}
    assert run_clean(llm=fake_llm(ok, CLEAN_EDITOR))["ok"] is True
    para = {"claims": [dict(CLEAN_VERIFIER["claims"][0],
                            quote="Conduct a regular inventory of data, noting where it is collected")]}
    assert run_clean(llm=fake_llm(para, CLEAN_EDITOR))["ok"] is False


@pytest.mark.parametrize("claim,needle", [
    ({"verdict": "NOT_SUPPORTED", "source": FTC, "quote": ""}, "claim is not supported by its source"),
    ({"verdict": "UNVERIFIABLE", "source": None, "quote": ""}, "unverifiable claim"),
    ({"verdict": "PROBABLY", "source": FTC, "quote": ""}, "unverifiable claim"),
    ({"verdict": "SUPPORTED", "source": FTC, "quote": "inventory"}, "without a usable quoted passage"),
    ({"verdict": "SUPPORTED", "source": "https://not-in-the-post.example/", "quote": "Conduct a periodic inventory of data"},
     "quoted passage is not in the source text"),
])
def test_anything_short_of_a_code_confirmed_supported_is_a_violation_naming_the_sentence(claim, needle):
    reply = {"claims": [{"sentence_id": "S5", "claim": "c", "kind": "external", **claim}]}
    r = run_clean(llm=fake_llm(reply, CLEAN_EDITOR))
    assert r["ok"] is False
    assert any(needle in v and "The FTC tells covered businesses" in v for v in r["violations"])


def test_brand_claims_are_supported_only_by_a_passage_in_the_facts_file():
    reply = {"claims": [{"sentence_id": "S6", "claim": "starts with a diagnostic call", "kind": "brand",
                         "source": "facts", "verdict": "SUPPORTED",
                         "quote": "Every engagement starts with a 30-minute diagnostic call."}]}
    facts = "Every engagement starts with a 30-minute diagnostic call."
    assert run_clean(llm=fake_llm(reply, CLEAN_EDITOR), facts_text=facts)["ok"] is True
    r = run_clean(llm=fake_llm(reply, CLEAN_EDITOR), facts_text="")
    assert r["ok"] is False and any("not in the source text" in v for v in r["violations"])


@pytest.mark.parametrize("reply", [None, [], {}, {"claims": "none"}, {"claims": ["S1"]}])
def test_a_malformed_verifier_reply_holds_the_post(reply):
    r = run_clean(llm=fake_llm(reply, CLEAN_EDITOR))
    assert r["ok"] is False and r["error"] and "HELD" in r["violations"][0]


# ------------------------------------------------------------------ editor

def test_an_editor_fail_is_a_violation_with_its_evidence_line():
    ed = editor_ok("No single dealership system owns the customer record by default.",
                   "The FTC tells covered businesses to keep a periodic inventory of customer data")
    ed["items"][3] = {"id": "no_unsupported_absolutes", "pass": False,
                      "evidence": "Most dealerships never formally decide this."}
    r = run_clean(llm=fake_llm(CLEAN_VERIFIER, ed))
    assert r["ok"] is False and r["counts"]["editor_fails"] == 1
    assert any(v.startswith("editor: no_unsupported_absolutes FAIL")
               and "Most dealerships never formally decide this." in v for v in r["violations"])


def test_a_pass_whose_evidence_is_not_in_the_post_counts_as_a_fail():
    ed = editor_ok("No single dealership system owns the customer record by default.",
                   "Our last 40 installs averaged nine days from contract to first call.")
    r = run_clean(llm=fake_llm(CLEAN_VERIFIER, ed))
    assert r["ok"] is False
    assert any("concrete_specific was passed with evidence that is not in the post" in v
               for v in r["violations"])


def test_editor_cannot_pass_no_em_dashes_when_one_is_present():
    post = dict(CLEAN_POST, description=f"A field by field answer {EM_DASH} finally.")
    r = run_clean(post=post)
    assert any(v.startswith("editor: no_em_dashes FAIL") for v in r["violations"])


def test_every_rubric_item_must_be_answered_or_the_post_holds():
    ed = editor_ok("No single dealership system owns the customer record by default.",
                   "The FTC tells covered businesses to keep a periodic inventory of customer data")
    ed["items"].pop()
    r = run_clean(llm=fake_llm(CLEAN_VERIFIER, ed))
    assert r["ok"] is False and "operator_voice" in r["error"]
    ed2 = editor_ok("a", "b")
    ed2["items"][0]["pass"] = "yes"
    assert run_clean(llm=fake_llm(CLEAN_VERIFIER, ed2))["error"]


def test_rubric_covers_the_seven_required_items():
    assert [r[0] for r in fc.EDITOR_RUBRIC] == [
        "answers_title", "concrete_specific", "no_filler_or_hype", "no_unsupported_absolutes",
        "no_invented_timelines", "no_em_dashes", "operator_voice"]


# ------------------------------------------------------------------ kill-safe + model separation

def test_model_error_holds_the_post():
    r = run_clean(llm=fake_llm(RuntimeError("LLM 529: overloaded"), CLEAN_EDITOR))
    assert r["ok"] is False and "RuntimeError" in r["error"]
    assert r["violations"] == [f"factcheck gate error (post HELD, never published unchecked): {r['error']}"]


def test_network_down_holds_the_post_without_guessing():
    http = fake_http({}, default=ConnectionError("network is unreachable"))
    r = fc.run_gate(CLEAN_POST, AVI_CFG, http_get=http, use_llm=False, facts_text="")
    assert r["ok"] is False and r["counts"]["links_unchecked"] == 2 and r["counts"]["links_dead"] == 0


def test_run_gate_never_raises_even_on_garbage():
    for post in (None, "text", {"body": "not a list"}, {"body_mdx": 5}):
        r = fc.run_gate(post, AVI_CFG, http_get=fake_http({}), llm=fake_llm({}, {}), facts_text="")
        assert r["ok"] is False and r["violations"]


def test_verifier_must_be_a_different_model_than_the_writer(monkeypatch):
    from services import slipstream_generate as gen
    monkeypatch.setattr(gen, "_MODEL", "claude-sonnet-5")
    with pytest.raises(fc.GateError, match="same as the writer model"):
        fc.default_llm("claude-sonnet-5")
    assert callable(fc.default_llm("claude-opus-5-5"))
    # and through the gate, a misconfiguration HOLDS rather than self-grading
    monkeypatch.setenv("SLIPSTREAM_VERIFIER_MODEL", "claude-sonnet-5")
    r = fc.run_gate(CLEAN_POST, AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), facts_text="")
    assert r["ok"] is False and "same as the writer model" in r["error"]


def test_verifier_model_default_and_env_override(monkeypatch):
    from services import slipstream_generate as gen
    monkeypatch.delenv("SLIPSTREAM_VERIFIER_MODEL", raising=False)
    assert fc.verifier_model() == "claude-opus-5-5" != gen._MODEL
    monkeypatch.setenv("SLIPSTREAM_VERIFIER_MODEL", "claude-haiku-5-5")
    assert fc.verifier_model() == "claude-haiku-5-5"


def test_default_llm_sends_the_verifier_model_over_the_writers_transport(monkeypatch):
    from services import slipstream_generate as gen
    sent = {}

    class R:
        ok = True

        def json(self):
            return {"id": "m", "model": "claude-opus-5-5", "usage": {},
                    "content": [{"type": "thinking", "thinking": ""},
                                {"type": "text", "text": '{"claims": []}'}]}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, json=json, timeout=timeout, headers=headers)
        return R()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(gen.requests, "post", post)
    assert fc.default_llm("claude-opus-5-5")("sys", "usr") == {"claims": []}
    assert sent["json"]["model"] == "claude-opus-5-5" and sent["json"]["system"] == "sys"
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert "temperature" not in sent["json"]          # rejected by current models
    # the writer path is unchanged: no model argument means the writer model
    gen._llm_json_once("s", "u")
    assert sent["json"]["model"] == gen._MODEL


def test_paid_model_calls_are_skipped_when_asked_and_the_code_checks_already_fail():
    llm = fake_llm(CLEAN_VERIFIER, CLEAN_EDITOR)
    bad = _fig_post("Most setups go live in one to three business days.")
    r = fc.run_gate(bad, AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), llm=llm, facts_text="",
                    skip_llm_on_deterministic_fail=True)
    assert r["ok"] is False and llm.calls == [] and r["llm_ran"] is False
    # first pass (default) still runs them, so one revision gets the full list
    r = fc.run_gate(bad, AVI_CFG, http_get=fake_http(dict(CLEAN_PAGES)), llm=llm, facts_text="")
    assert len(llm.calls) == 2 and r["ok"] is False


# ------------------------------------------------------------------ facts file + config

def test_parse_facts_keeps_fact_lines_and_drops_documentation():
    raw = "<!--\n- Example: costs $5 a month.\n-->\n# Approved facts\n\n- Setup takes 3 business days.\nNo setup fee.\n"
    assert fc.parse_facts(raw) == "Setup takes 3 business days.\nNo setup fee."


def test_starter_facts_files_are_documented_and_hold_no_invented_facts():
    """A brand with NO facts file is safe (no facts means no brand claims), so
    a newly added brand does not need one to pass. The four brands that were
    enabled when the gate shipped each get a documented, empty starter."""
    import os
    for key in ("autointelligence", "aiphoneguy", "worshipdigital", "paperandpurpose"):
        assert os.path.exists(os.path.join(fc._REPO_ROOT, f"config/slipstream_facts/{key}.md")), key
    for key in se._load_cfg()["brands"]:
        cfg = se._brand_cfg(key)
        path = os.path.join(fc._REPO_ROOT, cfg["factcheck"]["facts_file"])
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read()
            assert "APPROVED BRAND FACTS" in raw and EM_DASH not in raw
            assert fc.load_facts(cfg) == "", f"{key}: starter file must hold no invented facts"


def test_missing_facts_file_is_just_no_facts():
    assert fc.load_facts({"brand_key": "nope", "factcheck": {"facts_file": "config/slipstream_facts/nope.md"}}) == ""


def test_brand_cfg_merges_defaults_with_brand_rules():
    assert len(_real("aiphoneguy")["factcheck"]["banned"]) == 5
    assert _real("aiphoneguy")["factcheck"]["figure_allowlist"] == ["24/7"]
    assert _real("worshipdigital")["factcheck"]["facts_file"] == "config/slipstream_facts/worshipdigital.md"
    assert fc.resolve_factcheck_cfg({}, "x")["banned"] == []


def test_no_em_dashes_in_any_prompt_or_rule_text_this_gate_ships():
    import json
    blob = fc.VERIFIER_SYSTEM + fc.EDITOR_SYSTEM + json.dumps(fc.EDITOR_RUBRIC) \
        + json.dumps(se._load_cfg()["factcheck"], ensure_ascii=False)
    assert EM_DASH not in blob


# ------------------------------------------------------------------ summary + PR table

def test_summary_and_pr_table():
    r = run_clean()
    s = fc.summarize([r], revised=False)
    assert s["ran"] and s["passed"] and s["revised"] is False and s["passes"] == 1
    assert s["model"] == "fake-verifier" and s["counts"]["claims"] == 1
    table = fc.pr_table(s)
    assert "| Claim | Source | Verdict |" in table and FTC in table and "SUPPORTED" in table
    assert "`fake-verifier`" in table and "revised once by the writer: no" in table
    assert fc.pr_table(None) == "" and EM_DASH not in table
    assert "a \\| b" in fc.pr_table({"ran": True, "counts": {}, "claims": [
        {"claim": "a | b", "source": "", "verdict": "SUPPORTED"}]})
