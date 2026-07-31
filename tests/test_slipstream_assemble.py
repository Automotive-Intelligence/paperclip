from services.slipstream_assemble import assemble_mdx, normalize_console_diagram

POST = {
    "title": "What Should a Dealer Map Before Buying AI",
    "description": "A diagnostic-first orientation.",
    "slug": "what-to-map-before-buying-ai",
    "body_mdx": (
        "<AnswerFirst>Map the handoffs first.</AnswerFirst>\n\n"
        '<EntityDefinition term="Orchestration">routing between systems.</EntityDefinition>\n\n'
        "## Where do conversations get dropped?\n\nAt the handoff.\n\n"
        '<ConsoleDiagram steps="In | Route | Confirm" />\n\n'
        '<img src="/blog/what-to-map-before-buying-ai-gap.png" alt="a gap" />\n\n'
        "<PullQuote>You cannot fix what you never measured.</PullQuote>\n\n"
        '<img src="/blog/what-to-map-before-buying-ai-flow.png" alt="a flow" />\n\n'
        "See our [diagnostic call](/diagnostic-call) and [Cox](https://www.coxautoinc.com).\n"
    ),
    "image_prompts": [{"name": "hero", "prompt": "x"}, {"name": "gap", "prompt": "y"},
                      {"name": "flow", "prompt": "z"}],
    "social": {"linkedin": "li", "x": "x"},
}


def test_assemble_produces_valid_mdx_with_frontmatter():
    mdx, violations = assemble_mdx(POST, date_str="2026-07-19")
    assert violations == [], f"unexpected violations: {violations}"
    assert mdx.startswith("---\n")
    assert 'title: "What Should a Dealer Map Before Buying AI"' in mdx
    assert 'author: "Michael Rodriguez"' in mdx
    assert 'heroImage: "/blog/what-to-map-before-buying-ai-hero.png"' in mdx
    assert 'date: "2026-07-19"' in mdx
    assert "<AnswerFirst>" in mdx


def test_assemble_surfaces_gate_violations():
    bad = dict(POST, body_mdx=POST["body_mdx"].replace("<PullQuote>You cannot fix what you never measured.</PullQuote>", ""))
    mdx, violations = assemble_mdx(bad, date_str="2026-07-19")
    assert any("pullquote" in v.lower() for v in violations)


def test_assemble_hero_image_path_matches_slug():
    mdx, _ = assemble_mdx(POST, date_str="2026-07-19")
    assert "/blog/what-to-map-before-buying-ai-hero.png" in mdx  # path present


def test_colon_in_title_produces_valid_yaml():
    import yaml
    post = dict(POST, title="Signal vs. Noise: How to Tell if AI Works")
    mdx, violations = assemble_mdx(post, date_str="2026-07-19")
    fm = mdx.split("---", 2)[1]
    loaded = yaml.safe_load(fm)  # must not raise (the real build-breaker)
    assert loaded["title"] == "Signal vs. Noise: How to Tell if AI Works"


# --- ConsoleDiagram normalization (bug 2: bare-brace MDX child crashes build) ---

def test_console_diagram_json_child_normalized_to_valid_mdx():
    """The <ConsoleDiagram>{...raw JSON...}</ConsoleDiagram> form the LLM emits is
    rewritten to the valid self-closing pipe-string form, and the post passes."""
    bad_body = POST["body_mdx"].replace(
        '<ConsoleDiagram steps="In | Route | Confirm" />',
        '<ConsoleDiagram>{"steps": ["Lead in", "Route", "Confirm"], "caption": "the flow"}</ConsoleDiagram>',
    )
    mdx, violations = assemble_mdx(dict(POST, body_mdx=bad_body), date_str="2026-07-30")
    # no bare-brace child survives (that is exactly what crashes the MDX build)
    assert "</ConsoleDiagram>" not in mdx
    assert "ConsoleDiagram>{" not in mdx
    # rewritten to the valid self-closing pipe-delimited form
    assert '<ConsoleDiagram steps="Lead in | Route | Confirm" caption="the flow" />' in mdx
    assert violations == [], f"unexpected violations: {violations}"


def test_console_diagram_unrecoverable_child_dropped_no_bare_brace():
    """A JSON child with no recoverable steps is dropped rather than shipped as a
    bare brace; a Callout keeps the required visual so the post still passes."""
    bad_body = POST["body_mdx"].replace(
        '<ConsoleDiagram steps="In | Route | Confirm" />',
        '<ConsoleDiagram>{"title": "nope"}</ConsoleDiagram>\n\n<Callout>keeps a visual element</Callout>',
    )
    mdx, violations = assemble_mdx(dict(POST, body_mdx=bad_body), date_str="2026-07-30")
    assert "</ConsoleDiagram>" not in mdx
    assert "ConsoleDiagram>{" not in mdx
    assert violations == [], f"unexpected violations: {violations}"


def test_normalize_leaves_valid_selfclosing_untouched():
    body = '<ConsoleDiagram steps="A | B | C" caption="x" />'
    assert normalize_console_diagram(body) == body
