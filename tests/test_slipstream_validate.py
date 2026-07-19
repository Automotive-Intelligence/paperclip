from services.slipstream_validate import validate_post

# A clean, Slipstream-compliant MDX post (frontmatter + body).
GOOD = '''---
title: What Should a Dealer Map Before Buying AI
description: A diagnostic-first orientation for dealers evaluating AI tools.
date: 2026-07-19
author: Michael Rodriguez
heroImage: /blog/what-to-map-hero.png
---

<AnswerFirst>Map the handoffs first. Most dealership AI fails at the seams between systems, not inside them.</AnswerFirst>

<EntityDefinition term="Orchestration">The layer that routes a customer between systems without dropping context.</EntityDefinition>

## Where do dealership conversations get dropped?

They get dropped at the handoff. Here is the order of operations.

<ConsoleDiagram steps="Lead in | Route | Confirm | Follow up" />

<img src="/blog/what-to-map-gap.png" alt="a diagram of a gap between two systems" />

<PullQuote>You cannot fix a handoff you never measured.</PullQuote>

<img src="/blog/what-to-map-flow.png" alt="a flow of a customer path" />

See our [diagnostic call](/diagnostic-call) and [Cox Automotive](https://www.coxautoinc.com).
'''


def test_clean_post_passes():
    assert validate_post(GOOD) == []


def test_em_dash_flagged():
    bad = GOOD.replace("not inside them.", "not inside them — really.")
    v = validate_post(bad)
    assert any("em-dash" in x.lower() for x in v)


def test_missing_hero_image_flagged():
    bad = GOOD.replace("heroImage: /blog/what-to-map-hero.png\n", "")
    v = validate_post(bad)
    assert any("hero" in x.lower() for x in v)


def test_too_few_in_body_images_flagged():
    bad = GOOD.replace('<img src="/blog/what-to-map-flow.png" alt="a flow of a customer path" />', "")
    v = validate_post(bad)
    assert any("in-body image" in x.lower() for x in v)


def test_console_diagram_array_prop_flagged():
    bad = GOOD.replace('steps="Lead in | Route | Confirm | Follow up"', 'steps={["Lead in", "Route"]}')
    v = validate_post(bad)
    assert any("consolediagram" in x.lower() and "array" in x.lower() for x in v)


def test_missing_answerfirst_flagged():
    bad = GOOD.replace("<AnswerFirst>Map the handoffs first. Most dealership AI fails at the seams between systems, not inside them.</AnswerFirst>", "")
    v = validate_post(bad)
    assert any("answerfirst" in x.lower() for x in v)


def test_missing_required_frontmatter_flagged():
    bad = GOOD.replace("author: Michael Rodriguez\n", "")
    v = validate_post(bad)
    assert any("author" in x.lower() for x in v)


def test_missing_pullquote_flagged():
    bad = GOOD.replace("<PullQuote>You cannot fix a handoff you never measured.</PullQuote>", "")
    v = validate_post(bad)
    assert any("pullquote" in x.lower() for x in v)


def test_unclosed_answerfirst_flagged():
    # the real 2026-07-19 build-breaker: <AnswerFirst> opened, never closed
    bad = GOOD.replace(
        "<AnswerFirst>Map the handoffs first. Most dealership AI fails at the seams between systems, not inside them.</AnswerFirst>",
        "Map the handoffs first.<AnswerFirst>")
    v = validate_post(bad)
    assert any("answerfirst" in x.lower() and ("unbalanced" in x.lower() or "closed" in x.lower()) for x in v)


def test_unclosed_entitydefinition_flagged():
    # the BAE 2026-07-19 build-breaker: <EntityDefinition ...> opened, never closed
    bad = GOOD.replace(
        '<EntityDefinition term="Orchestration">The layer that routes a customer between systems without dropping context.</EntityDefinition>',
        '<EntityDefinition term="Orchestration">The layer that routes a customer.')
    v = validate_post(bad)
    assert any("entitydefinition" in x.lower() and "unbalanced" in x.lower() for x in v)
