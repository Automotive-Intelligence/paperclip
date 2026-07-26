"""TDD for validate_blocks -- the block-level publish gate for ts_posts_array (WD).

Parallel to validate_post (the MDX gate). Requires: exactly one LEADING answer
block, >=1 definition, >=1 quote, >=1 callout, heroImage set, >=2 image blocks,
and no em-dash anywhere. Any violation HOLDS the post.
"""
from services.slipstream_validate import validate_blocks


def _good():
    return {
        "slug": "s",
        "title": "A plain title",
        "description": "A plain description.",
        "heroImage": "/blog/s-hero.png",
        "body": [
            {"type": "answer", "text": "the short answer"},
            {"type": "definition", "term": "X", "text": "a definition"},
            {"type": "h2", "text": "What matters here?"},
            {"type": "callout", "title": "k", "text": "an insight"},
            {"type": "quote", "text": "a pull quote"},
            {"type": "image", "src": "/blog/s-a.png", "alt": "a"},
            {"type": "image", "src": "/blog/s-b.png", "alt": "b"},
            {"type": "p", "text": "closing text"},
        ],
    }


def test_good_block_set_passes():
    assert validate_blocks(_good()) == []


def test_first_block_must_be_answer():
    p = _good()
    p["body"].insert(0, {"type": "p", "text": "intro before the answer"})
    assert any("answer" in v.lower() for v in validate_blocks(p))


def test_exactly_one_answer_block():
    p = _good()
    p["body"].append({"type": "answer", "text": "a second answer block"})
    assert any("answer" in v.lower() for v in validate_blocks(p))


def test_missing_definition_flagged():
    p = _good()
    p["body"] = [b for b in p["body"] if b["type"] != "definition"]
    assert any("definition" in v.lower() for v in validate_blocks(p))


def test_missing_quote_flagged():
    p = _good()
    p["body"] = [b for b in p["body"] if b["type"] != "quote"]
    assert any("quote" in v.lower() for v in validate_blocks(p))


def test_missing_callout_flagged():
    p = _good()
    p["body"] = [b for b in p["body"] if b["type"] != "callout"]
    assert any("callout" in v.lower() for v in validate_blocks(p))


def test_missing_hero_flagged():
    p = _good()
    p["heroImage"] = ""
    assert any("hero" in v.lower() for v in validate_blocks(p))


def test_too_few_images_flagged():
    p = _good()
    imgs = [b for b in p["body"] if b["type"] == "image"]
    p["body"].remove(imgs[0])  # leave only one image block
    assert any("image" in v.lower() for v in validate_blocks(p))


def test_em_dash_in_body_flagged():
    p = _good()
    p["body"][-1]["text"] = "closing text with an em dash — right here"
    assert any("em-dash" in v.lower() for v in validate_blocks(p))


def test_em_dash_in_title_flagged():
    p = _good()
    p["title"] = "Title — with a dash"
    assert any("em-dash" in v.lower() for v in validate_blocks(p))


def test_em_dash_nested_in_table_flagged():
    p = _good()
    p["body"].append({"type": "table", "headers": ["A", "B"],
                      "rows": [["ok", "has — dash"]]})
    assert any("em-dash" in v.lower() for v in validate_blocks(p))
