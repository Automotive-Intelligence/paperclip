"""Research before writing (services/slipstream_research) and how run_brand,
generate_post and revise_post use the source pack. All offline: fake HTTP, fake
search, fake LLM."""
import json
from unittest import mock

import pytest

from services import slipstream_engine as se
from services import slipstream_factcheck as fc
from services import slipstream_generate as gen
from services import slipstream_research as rs

EM_DASH = chr(0x2014)

FILLER = ("This page explains the rule in plain language for small business owners and the "
          "people who advise them. ") * 8
NIST = ("https://www.nist.gov/guide", FILLER + "The framework has five core functions that every "
        "program should cover. Small firms reported 43 percent fewer incidents after adopting it. "
        "Published March 3, 2026.")
TRADE = ("https://trade.example/report", FILLER + "Dealers must keep a written inventory of the "
         "customer data they hold. The survey covered 1,200 stores across the country.")
BLOG = ("https://seo-blog.example/top-10", FILLER + "Ten tips you will love about compliance.")
PAGES = dict([NIST, TRADE, BLOG])

_CFG = {"brand_key": "autointelligence", "repo": "salesdroid/automotive-intelligence",
        "domain": "automotiveintelligence.io", "blog_dir": "src/content/blog",
        "business_key": "autointelligence", "money_pages": ["/services", "/diagnostic-call"],
        "voice": "diagnostic",
        "research": {"enabled": True, "max_searches": 3, "max_candidates": 10, "max_sources": 6,
                     "min_sources": 2, "blocked_domains": ["reddit.com"],
                     "preferred_domains": ["nist.gov"]}}

SITEMAP = ("<urlset><url><loc>https://automotiveintelligence.io/</loc></url>"
           "<url><loc>https://automotiveintelligence.io/blog/old-post</loc></url>"
           "<url><loc>https://www.automotiveintelligence.io/about/</loc></url>"
           "<url><loc>https://automotiveintelligence.io/gone</loc></url>"
           "<url><loc>https://elsewhere.example/x</loc></url></urlset>")


class R:
    def __init__(self, status=200, text="", ctype="text/html"):
        self.status_code, self.text, self.headers = status, text, {"content-type": ctype}


class Http:
    """Fake transport that records every URL it is asked for."""

    def __init__(self, pages=None, extra=None):
        self.calls = []
        self.pages = {
            "https://automotiveintelligence.io/sitemap.xml": R(200, SITEMAP, "application/xml"),
            "https://automotiveintelligence.io/": R(200, "home " * 200),
            "https://automotiveintelligence.io/services": R(200, "services " * 200),
            "https://automotiveintelligence.io/diagnostic-call": R(200, "call " * 200),
            "https://automotiveintelligence.io/about": R(200, "about " * 200),
            "https://automotiveintelligence.io/blog/old-post": R(200, "old " * 200),
            "https://automotiveintelligence.io/gone": R(404, "nope"),
        }
        for url, text in (pages if pages is not None else PAGES).items():
            self.pages[url] = R(200, f"<html><body><p>{text}</p></body></html>")
        self.pages.update(extra or {})

    def __call__(self, url):
        self.calls.append(url)
        r = self.pages.get(url)
        if isinstance(r, Exception):
            raise r
        return r or R(404, "not found")


def _search(*urls, searches=2):
    def _s(cfg, topic, rcfg):
        return {"candidates": [{"url": u, "title": f"title of {u}"} for u in urls],
                "searches": searches, "model": "research-model"}
    return _s


def _extract(quotes_by_url, dates=None):
    def _llm(system, user):
        assert system == rs.EXTRACT_SYSTEM
        return {"sources": [{"url": u, "publisher": "Pub", "date": (dates or {}).get(u, ""),
                             "quotes": q} for u, q in quotes_by_url.items()]}
    return _llm


GOOD_QUOTES = {
    NIST[0]: ["Small firms reported 43 percent fewer incidents after adopting it."],
    TRADE[0]: ["Dealers must keep a written inventory of the customer data they hold."],
}


def _pack(search=None, llm=None, http=None, cfg=None, cache=None):
    return rs.build_source_pack(
        cfg or _CFG, "a topic", http_get=http or Http(),
        search=search or _search(NIST[0], TRADE[0]),
        llm=llm or _extract(GOOD_QUOTES), link_cache=cache, facts_text="")


# ---------------------------------------------------------------------------
# config: who researches
# ---------------------------------------------------------------------------

def test_research_follows_the_gate_in_the_real_config():
    se._load_cfg.cache_clear()
    for brand in ("autointelligence", "aiphoneguy", "worshipdigital"):
        cfg = se._brand_cfg(brand)
        assert cfg["research"]["enabled"] is True and rs.research_enabled(cfg) is True
        assert cfg["research"]["max_searches"] <= 5, "search cap must stay small"
    pp = se._brand_cfg("paperandpurpose")
    assert pp["factcheck"]["enabled"] is False
    assert pp["research"]["enabled"] is False and rs.research_enabled(pp) is False


def test_research_enabled_defaults_to_the_gate_and_an_explicit_value_wins():
    assert rs.research_enabled({}) is True                                   # gated by default
    assert rs.research_enabled({"factcheck": {"enabled": False}}) is False   # opted out of the gate
    assert rs.research_enabled({"factcheck": {"enabled": False},
                                "research": {"enabled": True}}) is True
    assert rs.research_enabled({"research": {"enabled": False}}) is False
    assert rs.research_enabled({"research": {"enabled": "no"}}) is True      # only a real boolean counts


def test_resolve_research_cfg_adds_brand_lists_to_the_defaults():
    full = {"research": {"defaults": {"max_searches": 2, "blocked_domains": ["a.com"]},
                         "brands": {"x": {"blocked_domains": ["b.com"], "min_sources": 3},
                                    "off": {"enabled": False}}},
            "factcheck": {"brands": {"out": {"enabled": False}}}}
    x = rs.resolve_research_cfg(full, "x")
    assert x["max_searches"] == 2 and x["min_sources"] == 3 and x["enabled"] is True
    assert x["blocked_domains"] == ["a.com", "b.com"]
    assert rs.resolve_research_cfg(full, "off")["enabled"] is False
    assert rs.resolve_research_cfg(full, "out")["enabled"] is False


# ---------------------------------------------------------------------------
# candidates: what code keeps and drops
# ---------------------------------------------------------------------------

def test_a_pack_holds_verified_sources_quotes_figures_and_facts():
    pack = rs.build_source_pack(_CFG, "a topic", http_get=Http(), search=_search(TRADE[0], NIST[0]),
                                llm=_extract(GOOD_QUOTES, {NIST[0]: "March 3, 2026"}),
                                facts_text="Setup takes 5 business days.")
    assert pack["mode"] == "sourced" and pack["searches"] == 2
    assert [s["url"] for s in pack["sources"]] == [NIST[0], TRADE[0]], "primary source ranked first"
    nist = pack["sources"][0]
    assert nist["primary"] is True and nist["publisher"] == "Pub" and nist["date"] == "March 3, 2026"
    assert nist["quotes"][0]["text"].startswith("Small firms reported 43 percent")
    assert nist["figures"] == ["43 percent"]
    assert pack["facts"] == "Setup takes 5 business days."


def test_dead_link_is_dropped():
    pack = _pack(search=_search(NIST[0], TRADE[0], "https://dead.example/page"),
                 http=Http(extra={"https://dead.example/": R(200, "root is fine")}))
    assert [s["url"] for s in pack["sources"]] == [NIST[0], TRADE[0]]
    d = {x["url"]: x["reason"] for x in pack["dropped"]}
    assert d["https://dead.example/page"].startswith("dead link")


def test_a_source_the_gate_could_not_check_is_dropped():
    """403, a timeout, and the ftc.gov shape (404 for every path, root included)
    are all 'could not check' to the gate, so research never offers them."""
    http = Http(extra={"https://blocked.example/a": R(403, "forbidden"),
                       "https://www.ftc.gov/rule": R(404, "x"), "https://www.ftc.gov/": R(404, "x"),
                       "https://slow.example/a": TimeoutError("timed out")})
    pack = _pack(search=_search(NIST[0], "https://blocked.example/a", "https://www.ftc.gov/rule",
                                "https://slow.example/a", TRADE[0]), http=http)
    assert [s["url"] for s in pack["sources"]] == [NIST[0], TRADE[0]]
    d = {x["url"]: x["reason"] for x in pack["dropped"]}
    for u in ("https://blocked.example/a", "https://www.ftc.gov/rule", "https://slow.example/a"):
        assert d[u].startswith("could not check"), d[u]


def test_login_wall_thin_page_and_pdf_are_dropped():
    http = Http(extra={
        "https://wall.example/a": R(200, "<p>" + "Please sign in to continue reading. " * 20 + "</p>"),
        "https://thin.example/a": R(200, "<p>hello</p>"),
        "https://pdf.example/a.pdf": R(200, "%PDF-1.7 binary", "application/pdf"),
        "https://soft404.example/a": R(200, "<p>Page not found. " + "x " * 400 + "</p>")})
    pack = _pack(search=_search("https://wall.example/a", "https://thin.example/a",
                                "https://pdf.example/a.pdf", "https://soft404.example/a",
                                NIST[0], TRADE[0]), http=http)
    assert [s["url"] for s in pack["sources"]] == [NIST[0], TRADE[0]]
    d = {x["url"]: x["reason"] for x in pack["dropped"]}
    assert "login wall" in d["https://wall.example/a"]
    assert "almost no readable text" in d["https://thin.example/a"]
    assert "PDF" in d["https://pdf.example/a.pdf"]
    assert "not found" in d["https://soft404.example/a"]


def test_blocked_domains_and_the_brands_own_site_are_never_fetched_as_sources():
    http = Http()
    pack = _pack(search=_search("https://old.reddit.com/r/x", "https://automotiveintelligence.io/services",
                                NIST[0], NIST[0] + "#frag", TRADE[0], "ftp://x/y", "not a url"), http=http)
    assert [s["url"] for s in pack["sources"]] == [NIST[0], TRADE[0]]
    assert "https://old.reddit.com/r/x" not in http.calls
    reasons = {x["url"]: x["reason"] for x in pack["dropped"]}
    assert "blocklist" in reasons["https://old.reddit.com/r/x"]
    assert "own site" in reasons["https://automotiveintelligence.io/services"]
    assert http.calls.count(NIST[0]) == 1, "the same URL is fetched once"


def test_candidate_and_source_caps_bound_the_work():
    cfg = dict(_CFG, research=dict(_CFG["research"], max_candidates=1, max_sources=1))
    http = Http()
    pack = _pack(cfg=cfg, http=http, search=_search(TRADE[0], NIST[0]))
    assert [s["url"] for s in pack["sources"]] == [NIST[0]]          # the primary one survives the cap
    assert TRADE[0] not in http.calls and pack["mode"] == "qualitative"


# ---------------------------------------------------------------------------
# quotes: code confirms every one
# ---------------------------------------------------------------------------

def test_unconfirmed_quote_is_dropped_and_never_reaches_the_writer():
    invented = "Ninety percent of dealers fail an audit within one year of the rule."
    pack = _pack(llm=_extract({
        NIST[0]: [invented, "Small firms reported 43 percent fewer incidents after adopting it.",
                  "small firms   reported 43 percent fewer incidents after adopting it."],
        TRADE[0]: ["Dealers must keep a written inventory of the customer data they hold."]}))
    quotes = [q["text"] for s in pack["sources"] for q in s["quotes"]]
    assert invented not in quotes and len(pack["sources"][0]["quotes"]) == 1
    assert pack["quotes_discarded"] == 1
    assert "Ninety percent" not in rs.pack_prompt(pack, False)


def test_a_source_with_no_confirmed_quote_is_dropped_entirely():
    pack = _pack(llm=_extract({NIST[0]: ["Something the page does not say at all, anywhere."],
                               TRADE[0]: ["Dealers must keep a written inventory of the customer data they hold."]}))
    assert [s["url"] for s in pack["sources"]] == [TRADE[0]]
    assert any("no quote could be confirmed" in d["reason"] for d in pack["dropped"])


def test_quote_must_be_inside_the_window_the_gates_verifier_reads(monkeypatch):
    monkeypatch.setattr(fc, "_PROMPT_SOURCE_CHARS", 900)
    pack = _pack(llm=_extract({
        NIST[0]: ["Small firms reported 43 percent fewer incidents after adopting it."],   # past char 900
        TRADE[0]: ["This page explains the rule in plain language for small business owners"]}))
    assert [s["url"] for s in pack["sources"]] == [TRADE[0]]


def test_a_date_the_page_does_not_print_is_not_passed_on():
    pack = _pack(llm=_extract(GOOD_QUOTES, {NIST[0]: "January 1, 2020", TRADE[0]: ""}))
    assert pack["sources"][0]["date"] == ""


# ---------------------------------------------------------------------------
# thin pack: qualitative, never invent
# ---------------------------------------------------------------------------

def test_thin_pack_switches_the_writer_to_qualitative_mode_instead_of_failing():
    pack = _pack(search=_search(NIST[0]))
    assert pack["mode"] == "qualitative" and len(pack["sources"]) == 1
    prompt = rs.pack_prompt(pack, False)
    assert "THIN PACK" in prompt and "NO statistics" in prompt and "Do not fill the gap from memory" in prompt
    assert NIST[0] in prompt, "the one verified source may still be cited"


def test_empty_search_gives_an_empty_qualitative_pack_without_an_extraction_call():
    llm = mock.Mock()
    pack = _pack(search=_search(), llm=llm)
    llm.assert_not_called()
    assert pack["mode"] == "qualitative" and pack["sources"] == []
    prompt = rs.pack_prompt(pack, True)
    assert "SOURCES YOU MAY CITE: none." in prompt and "no external links at all" in prompt


def test_a_full_pack_prompt_carries_the_rules_urls_quotes_and_internal_links():
    pack = _pack()
    mdx, blocks = rs.pack_prompt(pack, False), rs.pack_prompt(pack, True)
    for p in (mdx, blocks):
        assert "Cite ONLY URLs listed in this pack" in p and "THIN PACK" not in p
        assert NIST[0] in p and TRADE[0] in p
        assert 'QUOTE: "Small firms reported 43 percent fewer incidents after adopting it."' in p
        assert "must appear in a pack quote or in APPROVED BRAND FACTS" in p
        assert "/services, /diagnostic-call" in p
        assert EM_DASH not in p
    assert "IN THAT SAME SENTENCE" in mdx and "'sources' block must list" in blocks


# ---------------------------------------------------------------------------
# internal links
# ---------------------------------------------------------------------------

def test_internal_links_are_verified_200_money_pages_first_then_the_sitemap():
    cache = {}
    pack = _pack(cache=cache)
    assert pack["internal_links"] == ["/services", "/diagnostic-call", "/", "/about", "/blog/old-post"]
    assert "/gone" not in pack["internal_links"], "a sitemap entry that 404s is never offered"
    # stored under the key the gate's check_links resolves a root-relative href to
    assert fc.resolve_link("/services", _CFG["domain"]) == ("https://automotiveintelligence.io/services", True)
    assert cache["https://automotiveintelligence.io/services"].internal is True


def test_a_money_page_that_does_not_load_is_not_offered_and_no_sitemap_is_fine():
    http = Http(extra={"https://automotiveintelligence.io/sitemap.xml": R(500, "boom"),
                       "https://automotiveintelligence.io/diagnostic-call": R(404, "gone")})
    pack = _pack(http=http)
    assert pack["internal_links"] == ["/services"]
    http2 = Http(extra={"https://automotiveintelligence.io/sitemap.xml": TimeoutError("t"),
                        "https://automotiveintelligence.io/services": R(503, "x"),
                        "https://automotiveintelligence.io/diagnostic-call": R(503, "x")})
    pack2 = _pack(http=http2)
    assert pack2["internal_links"] == []
    assert "use NO internal links" in rs.pack_prompt(pack2, False)


# ---------------------------------------------------------------------------
# research errors raise (the engine turns them into a HOLD)
# ---------------------------------------------------------------------------

def test_search_and_extraction_failures_raise_research_error():
    def boom(cfg, topic, rcfg):
        raise RuntimeError("api down")
    with pytest.raises(rs.ResearchError, match="search failed"):
        _pack(search=boom)
    with pytest.raises(rs.ResearchError, match="no candidate list"):
        _pack(search=lambda c, t, r: {"oops": 1})
    with pytest.raises(rs.ResearchError, match="quote extraction failed"):
        _pack(llm=mock.Mock(side_effect=gen.GenerationError("LLM 529")))
    with pytest.raises(rs.ResearchError, match="no 'sources' list"):
        _pack(llm=lambda s, u: {"nope": []})


# ---------------------------------------------------------------------------
# the production search call (request shape, pause_turn, fallbacks)
# ---------------------------------------------------------------------------

def _api(content, stop="end_turn", searches=1):
    resp = mock.Mock(ok=True, status_code=200)
    resp.json.return_value = {"id": "msg_1", "model": "m", "stop_reason": stop, "content": content,
                              "usage": {"input_tokens": 100, "output_tokens": 10,
                                        "server_tool_use": {"web_search_requests": searches}}}
    return resp


def test_default_search_uses_the_server_side_web_search_tool_with_a_cap(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    final = _api([{"type": "text", "text": json.dumps({"candidates": [{"url": NIST[0], "title": "t"}]})}])
    with mock.patch("requests.post", return_value=final) as post:
        out = rs.default_search(_CFG, "a topic", rs._rcfg(_CFG), model="some-model")
    body = post.call_args.kwargs["json"]
    assert post.call_args.args[0] == "https://api.anthropic.com/v1/messages"
    assert body["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3,
                              "blocked_domains": ["reddit.com"]}]
    assert body["model"] == "some-model" and "a topic" in body["messages"][0]["content"]
    assert "test-key" not in json.dumps(body)
    assert out == {"candidates": [{"url": NIST[0], "title": "t"}], "searches": 1, "model": "some-model"}
    assert gen.USAGE_LOG[-1]["web_searches"] == 1 and gen.USAGE_LOG[-1]["surface"] == "slipstream_research"


def test_default_search_resumes_a_paused_turn_and_stops_at_the_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    paused = _api([{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}], "pause_turn")
    final = _api([{"type": "text", "text": '{"candidates": []}'}], searches=2)
    with mock.patch("requests.post", side_effect=[paused, final]) as post:
        out = rs.default_search(_CFG, "t", rs._rcfg(_CFG))
    second = post.call_args_list[1].kwargs["json"]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"], "resume with no extra user turn"
    assert out["searches"] == 3
    with mock.patch("requests.post", return_value=paused):
        with pytest.raises(rs.ResearchError, match="did not finish"):
            rs.default_search(_CFG, "t", rs._rcfg(_CFG))


def test_default_search_errors_are_research_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(rs.ResearchError, match="ANTHROPIC_API_KEY missing"):
        rs.default_search(_CFG, "t", rs._rcfg(_CFG))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with mock.patch("requests.post", return_value=mock.Mock(ok=False, status_code=529, text="overloaded")):
        with pytest.raises(rs.ResearchError, match="529"):
            rs.default_search(_CFG, "t", rs._rcfg(_CFG))
    with mock.patch("requests.post", return_value=_api([], "refusal")):
        with pytest.raises(rs.ResearchError, match="refused"):
            rs.default_search(_CFG, "t", rs._rcfg(_CFG))


def test_candidates_fall_back_to_the_search_result_urls_when_the_model_returns_no_json():
    content = [
        {"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": NIST[0], "title": "N"}]},
        {"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error",
                                                       "error_code": "max_uses_exceeded"}},
        {"type": "text", "text": "Here is what I found."}]
    assert rs._candidates_from_response(content) == [{"url": NIST[0], "title": "N"}]
    fenced = [{"type": "text", "text": 'Sure {not json}\n```json\n{"candidates": [{"url": "https://a.example/x"}]}\n```'}]
    assert rs._candidates_from_response(fenced) == [{"url": "https://a.example/x"}]


# ---------------------------------------------------------------------------
# the writer: generate_post and revise_post
# ---------------------------------------------------------------------------

_MDX = {"title": "T", "description": "D", "slug": "my-post", "body_mdx": "<AnswerFirst>a</AnswerFirst>",
        "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"linkedin": "li", "x": "x"}}
_BLOCKS = {"title": "T", "description": "D", "slug": "my-post", "body": [{"type": "answer", "text": "a"}],
           "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"linkedin": "li"}}
_FORMATS = [(None, _MDX), ("ts_posts_array", _BLOCKS), ("tsx_post", _BLOCKS), ("shopify_article", _BLOCKS)]


@pytest.mark.parametrize("fmt,post", _FORMATS)
def test_generate_post_gives_the_writer_the_pack_in_every_format(fmt, post):
    cfg = dict(_CFG, **({"format": fmt} if fmt else {}))
    pack = _pack()
    with mock.patch.object(gen, "_llm_json", return_value=dict(post)) as llm:
        gen.generate_post(cfg, "a topic", pack=pack)
    user = llm.call_args.args[1]
    assert user.startswith("Write the post for this topic: a topic\nReturn only the JSON object.")
    assert "VERIFIED SOURCE PACK" in user and NIST[0] in user and "43 percent" in user
    assert "/services" in user and "Cite ONLY URLs listed in this pack" in user


@pytest.mark.parametrize("fmt,post", _FORMATS)
def test_without_a_pack_the_writer_prompt_is_byte_for_byte_the_old_one(fmt, post):
    cfg = dict(_CFG, **({"format": fmt} if fmt else {}))
    with mock.patch.object(gen, "_llm_json", return_value=dict(post)) as llm:
        gen.generate_post(cfg, "a topic")
        gen.generate_post(cfg, "a topic", pack=None)
    for call in llm.call_args_list:
        assert call.args[1] == "Write the post for this topic: a topic\nReturn only the JSON object."
        assert "SOURCE PACK" not in call.args[0]


@pytest.mark.parametrize("fmt,post", _FORMATS)
def test_revise_post_keeps_the_same_pack_so_a_revision_cannot_add_a_source(fmt, post):
    cfg = dict(_CFG, **({"format": fmt} if fmt else {}))
    pack = _pack()
    with mock.patch.object(gen, "_llm_json", return_value=dict(post)) as llm:
        gen.revise_post(cfg, "a topic", dict(post), ["dead source link: https://x"], pack=pack)
        gen.revise_post(cfg, "a topic", dict(post), ["dead source link: https://x"])
    with_pack, without = llm.call_args_list[0].args[1], llm.call_args_list[1].args[1]
    assert "VERIFIED SOURCE PACK" in with_pack and NIST[0] in with_pack and TRADE[0] in with_pack
    assert "Nothing outside the pack" in with_pack and "dead source link: https://x" in with_pack
    assert "SOURCE PACK" not in without and without.endswith("Return only the corrected JSON object.")


# ---------------------------------------------------------------------------
# post vs pack
# ---------------------------------------------------------------------------

def test_a_link_from_memory_and_an_unverified_internal_link_are_violations():
    pack = _pack()
    post = dict(_MDX, body_mdx=(
        f"<AnswerFirst>Keep [a written inventory]({TRADE[0]}/) of data.</AnswerFirst>\n"
        "Read [the rule](https://memory.example/made-up) and see [services](/services) "
        "or [pricing](/pricing-that-does-not-exist)."))
    v = rs.pack_violations(post, pack, _CFG["domain"])
    assert v == ["source link is not in the verified source pack (cited from memory): https://memory.example/made-up",
                 "internal link is not on the verified internal link list: /pricing-that-does-not-exist"]
    blocks = dict(_BLOCKS, body=[{"type": "answer", "text": "a"},
                                 {"type": "sources", "items": [{"label": "N", "href": NIST[0]},
                                                               {"label": "X", "href": "https://other.example/x"}]}])
    assert rs.pack_violations(blocks, pack, _CFG["domain"]) == [
        "source link is not in the verified source pack (cited from memory): https://other.example/x"]


# ---------------------------------------------------------------------------
# engine wiring
# ---------------------------------------------------------------------------

def _gate_ok(**kw):
    return {"ok": True, "violations": [], "error": None, "claims": [], "editor": [], "links": [],
            "counts": {}, "model": "claude-opus-5-5", "llm_ran": True, **kw}


class Engine:
    def __init__(self, pack=None, research_error=None, gate=None, post=None, cfg=None):
        self.order, self.gate_kw, self.generate_calls, self.revise_calls = [], [], [], []
        self.pack = pack
        self.research_error = research_error
        self.gate = list(gate or [_gate_ok()])
        self.post = post or _MDX
        self.cfg = cfg or _CFG

    def _research(self, cfg, topic, **kw):
        self.order.append("research")
        self.research_kw = kw
        if self.research_error:
            raise self.research_error
        kw["link_cache"]["https://www.nist.gov/guide"] = "FETCHED-BY-RESEARCH"
        return self.pack

    def _generate(self, *a, **kw):
        self.order.append("generate")
        self.generate_calls.append((a, kw))
        return dict(self.post)

    def _revise(self, *a, **kw):
        self.order.append("revise")
        self.revise_calls.append((a, kw))
        return dict(self.post)

    def _factcheck(self, post, cfg, **kw):
        self.order.append("factcheck")
        self.gate_kw.append(kw)
        return self.gate.pop(0)

    def _publish(self, repo, branch, files, title, body, token):
        self.order.append("publish")
        self.pr_body = body
        return "https://gh/pull/1"

    def run(self, real_switch=True):
        with mock.patch.object(se, "_brand_cfg", return_value=self.cfg), \
             mock.patch.object(se, "research_enabled", rs.research_enabled if real_switch else (lambda c: False)), \
             mock.patch.object(se, "build_source_pack", side_effect=self._research), \
             mock.patch.object(se, "generate_post", side_effect=self._generate), \
             mock.patch.object(se, "revise_post", side_effect=self._revise), \
             mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
             mock.patch.object(se, "assemble_mdx", return_value=("MDX", [])), \
             mock.patch.object(se, "run_factcheck", side_effect=self._factcheck), \
             mock.patch.object(se, "publish_post", side_effect=self._publish):
            return se.run_brand(self.cfg["brand_key"], topic="a topic", token="tok",
                                date_str="2026-11-15", auto_merge=False)


def test_research_runs_before_the_writer_and_the_pack_reaches_generate_and_the_gate():
    pack = _pack()
    e = Engine(pack=pack)
    out = e.run()
    assert e.order == ["research", "generate", "factcheck", "publish"]
    (args, kw), = e.generate_calls
    assert kw == {"pack": pack} and args[1] == "a topic"
    assert out["ok"] is True and out["research"]["mode"] == "sourced"
    assert [s["url"] for s in out["research"]["sources"]] == [NIST[0], TRADE[0]]
    # one cache object: research filled it, the gate receives the same dict
    assert e.gate_kw[0]["link_cache"] is e.research_kw["link_cache"]
    assert e.gate_kw[0]["link_cache"]["https://www.nist.gov/guide"] == "FETCHED-BY-RESEARCH"


def test_research_error_on_a_gated_brand_holds_and_the_writer_never_runs(caplog):
    e = Engine(research_error=rs.ResearchError("search LLM 529: overloaded"))
    out = e.run()
    assert e.order == ["research"], "no generate, no gate, no images, no PR"
    assert out["ok"] is False and out["held"] is True and out["reason"] == "research_error"
    assert out["violations"] == ["research failed (post HELD, never written from memory): "
                                 "ResearchError: search LLM 529: overloaded"]
    e2 = Engine(pack=None)                       # a broken research stage that returns nothing
    out2 = e2.run()
    assert e2.order == ["research"] and out2["held"] is True and out2["reason"] == "research_error"


def test_a_thin_pack_still_writes_in_qualitative_mode_rather_than_holding():
    pack = _pack(search=_search())
    e = Engine(pack=pack)
    out = e.run()
    assert e.order == ["research", "generate", "factcheck", "publish"]
    assert e.generate_calls[0][1]["pack"]["mode"] == "qualitative"
    assert out["ok"] is True and out["research"]["mode"] == "qualitative"
    assert "No usable source was found" in e.pr_body


def test_the_one_revision_gets_the_same_pack():
    pack = _pack()
    bad = _gate_ok(ok=False, violations=["dead source link (HTTP 404): https://x"])
    e = Engine(pack=pack, gate=[bad, _gate_ok()])
    out = e.run()
    assert e.order == ["research", "generate", "factcheck", "revise", "factcheck", "publish"]
    (args, kw), = e.revise_calls
    assert kw == {"pack": pack} and args[3] == ["dead source link (HTTP 404): https://x"]
    assert e.gate_kw[1]["link_cache"] is e.gate_kw[0]["link_cache"]
    assert out["ok"] is True and out["factcheck"]["revised"] is True


def test_a_link_outside_the_pack_holds_even_when_the_gate_itself_passes():
    pack = _pack()
    post = dict(_MDX, body_mdx="<AnswerFirst>See [this](https://memory.example/x).</AnswerFirst>")
    e = Engine(pack=pack, post=post, gate=[_gate_ok(), _gate_ok()])
    out = e.run()
    assert e.order == ["research", "generate", "factcheck", "revise", "factcheck"]
    assert out["held"] is True and "publish" not in e.order
    assert out["violations"] == ["source link is not in the verified source pack (cited from memory): "
                                 "https://memory.example/x"]
    assert out["research"]["mode"] == "sourced"


def test_pr_body_lists_the_pack_sources_next_to_the_claims_table():
    pack = _pack()
    claim = {"claim": "Dealers must keep an inventory", "source": TRADE[0], "verdict": "SUPPORTED", "quote": "q"}
    post = dict(_MDX, body_mdx=f"<AnswerFirst>Dealers must keep [an inventory]({TRADE[0]}).</AnswerFirst>")
    e = Engine(pack=pack, post=post, gate=[_gate_ok(claims=[claim], counts={"claims": 1})])
    e.run()
    body = e.pr_body
    assert "### Research before writing" in body and "### Fact-check gate" in body
    assert body.index("### Research before writing") < body.index("### Fact-check gate")
    assert f"| {NIST[0]} | Pub | 1 | no |" in body
    assert f"| {TRADE[0]} | Pub | 1 | yes |" in body
    assert "| Claim | Source | Verdict |" in body and "web searches: 2" in body
    assert EM_DASH not in body


def test_opted_out_brand_is_untouched_no_research_and_the_old_generate_call():
    """Paper & Purpose is opted out of the gate, so research is off for it:
    build_source_pack is never called, generate_post gets exactly the two
    positional arguments it always got, and the receipt has no research key."""
    se._load_cfg.cache_clear()
    pp_cfg = se._brand_cfg("paperandpurpose")
    post = {"title": "T", "description": "D", "slug": "s", "heroAlt": "h", "faq": [],
            "body": [{"type": "answer", "text": "a"}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"linkedin": "li"}}
    gen_mock = mock.Mock(return_value=dict(post))
    research = mock.Mock(side_effect=AssertionError("research must not run for an opted-out brand"))
    gate = mock.Mock(side_effect=AssertionError("the gate must not run for an opted-out brand"))
    with mock.patch.object(se, "research_enabled", rs.research_enabled), \
         mock.patch.object(se, "build_source_pack", research), \
         mock.patch.object(se, "run_factcheck", gate), \
         mock.patch.object(se, "generate_post", gen_mock), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_shopify_article", return_value=({"title": "T"}, [])), \
         mock.patch.object(se, "create_article_draft",
                           return_value={"ok": True, "dry_run": True, "blog_id": 1, "store": "s"}):
        out = se.run_brand("paperandpurpose", topic="a topic", token="tok", dry_run=True)
    gen_mock.assert_called_once_with(pp_cfg, "a topic")
    research.assert_not_called()
    assert out["ok"] is True and "research" not in out and "factcheck" not in out


def test_opted_out_brand_sends_the_writer_the_exact_prompt_it_sent_before_research_existed():
    se._load_cfg.cache_clear()
    pp_cfg = se._brand_cfg("paperandpurpose")
    post = {"title": "T", "description": "D", "slug": "s", "body": [{"type": "answer", "text": "a"}],
            "image_prompts": [{"name": "hero", "prompt": "h"}], "social": {"linkedin": "li"}}
    with mock.patch.object(se, "research_enabled", rs.research_enabled), \
         mock.patch.object(se, "build_source_pack", side_effect=AssertionError("no research")), \
         mock.patch.object(gen, "_llm_json", return_value=dict(post)) as llm, \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_shopify_article", return_value=({"title": "T"}, [])), \
         mock.patch.object(se, "create_article_draft",
                           return_value={"ok": True, "dry_run": True, "blog_id": 1, "store": "s"}):
        se.run_brand("paperandpurpose", topic="a topic", token="tok", dry_run=True)
    system, user = llm.call_args.args
    assert user == "Write the post for this topic: a topic\nReturn only the JSON object."
    assert system == gen._system_prompt_ts(pp_cfg) and "SOURCE PACK" not in system


def test_real_research_and_real_gate_share_one_fetch_per_url(monkeypatch):
    """End to end with the real build_source_pack and the real run_gate on one
    fake transport: the gate reuses what research fetched, and a post that cites
    only pack sources with a pack figure passes the code checks."""
    http = Http()
    monkeypatch.setattr(fc, "default_http_get", http)
    monkeypatch.setattr(rs, "default_search", _search(NIST[0], TRADE[0]))
    monkeypatch.setattr(rs, "default_llm", lambda model=None: _extract(GOOD_QUOTES))
    monkeypatch.setattr(fc, "default_llm", lambda model: (
        lambda s, u: {"claims": []} if s == fc.VERIFIER_SYSTEM else
        {"items": [{"id": rid, "pass": True, "evidence": "Small firms reported"} for rid, _q, _n in fc.EDITOR_RUBRIC]}))
    post = dict(_MDX, body_mdx=(
        f"<AnswerFirst>Small firms reported [43 percent fewer incidents]({NIST[0]}) after adopting it. "
        "See [services](/services).</AnswerFirst>"))
    with mock.patch.object(se, "_brand_cfg", return_value=_CFG), \
         mock.patch.object(se, "research_enabled", rs.research_enabled), \
         mock.patch.object(se, "run_factcheck", fc.run_gate), \
         mock.patch.object(se, "generate_post", return_value=post), \
         mock.patch.object(se, "generate_images", return_value={"hero": b"H"}), \
         mock.patch.object(se, "assemble_mdx", return_value=("MDX", [])), \
         mock.patch.object(se, "publish_post", return_value="https://gh/pull/1"):
        out = se.run_brand("autointelligence", topic="a topic", token="tok", auto_merge=False)
    assert out["ok"] is True, out
    assert http.calls.count(NIST[0]) == 1 and http.calls.count("https://automotiveintelligence.io/services") == 1
    assert out["factcheck"]["counts"]["figures_checked"] == 1
    assert out["factcheck"]["counts"]["figures_unmatched"] == 0


# ---------------------------------------------------------------------------
# the end-to-end dry-run script
# ---------------------------------------------------------------------------

def _script():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "slipstream_gate_check.py")
    spec = importlib.util.spec_from_file_location("slipstream_gate_check_research", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dry_run_write_runs_research_write_gate_and_publishes_nothing(monkeypatch, capsys, tmp_path):
    script = _script()
    pack = _pack()
    forbidden = mock.Mock(side_effect=AssertionError("a dry run must not publish"))
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    monkeypatch.setattr(se, "_brand_cfg", lambda b: _CFG)
    monkeypatch.setattr(se, "research_enabled", rs.research_enabled)
    monkeypatch.setattr(se, "build_source_pack", lambda cfg, topic, **kw: pack)
    monkeypatch.setattr(se, "generate_post", lambda cfg, topic, pack=None: dict(_MDX))
    monkeypatch.setattr(se, "assemble_mdx", lambda p, d: ("MDX", []))
    monkeypatch.setattr(se, "_next_topic", lambda cfg, token: "next queue topic")
    monkeypatch.setattr(se, "generate_images", forbidden)
    monkeypatch.setattr(se, "publish_post", forbidden)
    monkeypatch.setattr(se, "_checkoff_topic", forbidden)
    monkeypatch.setattr(se, "merge_when_green", forbidden)
    monkeypatch.setattr(se, "_distribute_social", forbidden)
    code = script.main(["--write", "--brand", "autointelligence", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    forbidden.assert_not_called()
    assert code == 0 and "VERDICT PASS" in out and "TOPIC   next queue topic" in out
    assert f"SOURCE  primary {NIST[0]}" in out and "COST    about $0.00" in out
    saved = json.loads((tmp_path / "autointelligence.json").read_text())
    assert saved["topic"] == "next queue topic" and saved["pack"]["mode"] == "sourced"
    assert "### Research before writing" in saved["pr_body"]


def test_dry_run_write_reports_a_hold_with_the_violations(monkeypatch, capsys):
    script = _script()
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "tok")
    monkeypatch.setattr(se, "_brand_cfg", lambda b: _CFG)
    monkeypatch.setattr(se, "research_enabled", rs.research_enabled)
    monkeypatch.setattr(se, "build_source_pack", mock.Mock(side_effect=rs.ResearchError("search LLM 529")))
    code = script.main(["--write", "--brand", "autointelligence", "--topic", "t"])
    out = capsys.readouterr().out
    assert code == 1 and "VERDICT HOLD  reason=research_error" in out
    assert "research failed (post HELD, never written from memory): ResearchError: search LLM 529" in out


def test_cost_report_prices_tokens_and_web_searches():
    script = _script()
    r = script.cost_report([
        {"model": "claude-opus-5-5", "surface": "slipstream_research", "input_tokens": 100_000,
         "output_tokens": 1_000, "web_searches": 4},
        {"model": "claude-sonnet-5", "surface": "slipstream", "input_tokens": 10_000, "output_tokens": 5_000},
        {"model": "mystery", "surface": "x", "input_tokens": 1, "output_tokens": 1}])
    assert r["total_usd"] == round(0.4 + 0.02 + 0.04 + 0.02 + 0.05, 4)
    assert r["unpriced_models"] == ["mystery"]
