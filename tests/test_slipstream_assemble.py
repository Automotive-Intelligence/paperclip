from services.slipstream_assemble import assemble_mdx

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
    assert "title: What Should a Dealer Map Before Buying AI" in mdx
    assert "author: Michael Rodriguez" in mdx
    assert "heroImage: /blog/what-to-map-before-buying-ai-hero.png" in mdx
    assert "date: 2026-07-19" in mdx
    assert "<AnswerFirst>" in mdx


def test_assemble_surfaces_gate_violations():
    bad = dict(POST, body_mdx=POST["body_mdx"].replace("<PullQuote>You cannot fix what you never measured.</PullQuote>", ""))
    mdx, violations = assemble_mdx(bad, date_str="2026-07-19")
    assert any("pullquote" in v.lower() for v in violations)


def test_assemble_hero_image_path_matches_slug():
    mdx, _ = assemble_mdx(POST, date_str="2026-07-19")
    assert "/blog/what-to-map-before-buying-ai-hero.png" in mdx
