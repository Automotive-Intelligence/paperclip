"""TDD for the ts_posts_array serializer/assembler (WD onboarding).

Serializing a Post into a TS object literal must (a) escape special chars, (b)
splice newest-first into a fixture copy of posts.ts yielding array length +1 with
a unique slug, and (c) parse as real JS (node) when available. Fully hermetic:
the current-file fetch is injected, no network.
"""
import shutil
import subprocess

import pytest

from services.slipstream_assemble import assemble_ts_posts, _ts_serialize

# A minimal but structurally-real posts.ts: the exact anchor line + 2 posts, each
# with a single top-level `slug: "..."` (block objects never carry a slug, so
# counting `slug: "` reliably counts posts).
FIXTURE = '''// Migrated posts, WD voice.
export type Block = { type: "p"; text: string };
export type Post = { slug: string; title: string; body: Block[] };

export const POSTS: Post[] = [
  {
    slug: "existing-one",
    title: "Existing One",
    body: [{ type: "p", text: "hi" }],
  },
  {
    slug: "existing-two",
    title: "Existing Two",
    body: [{ type: "p", text: "yo" }],
  },
];

export const getPost = (slug) => POSTS.find((p) => p.slug === slug);
'''

CFG = {"repo": "salesdroid/worship-digital", "posts_file": "src/content/posts.ts",
       "format": "ts_posts_array", "business_key": "worshipdigital"}

SEP = chr(1)  # a byte that never appears in the content, used as a field delimiter


def _fetch_fixture(cfg, token):
    return FIXTURE


def _good_post(slug="brand-new-post"):
    # Title packs every dangerous char: double-quote, backslash, template `$`,
    # backtick, so the escaping is exercised end to end.
    return {
        "slug": slug,
        "title": 'Quotes "inside" and a backslash \\ and a $var and `tick`',
        "description": "A plain description.",
        "category": "Working With an Agency",
        "heroAlt": "hero alt text",
        "ogTitle": "OG title",
        "body": [
            {"type": "answer", "text": "The short answer, with a $ and a `tick`."},
            {"type": "definition", "term": "Thing", "text": "a thing"},
            {"type": "callout", "title": "Key", "text": "the insight"},
            {"type": "quote", "text": "a pull quote"},
            {"type": "image", "src": f"/blog/{slug}-a.png", "alt": "a"},
            {"type": "image", "src": f"/blog/{slug}-b.png", "alt": "b", "caption": "cap"},
            {"type": "p", "text": "closing body text"},
        ],
    }


def test_ts_serialize_escapes_special_chars():
    s = _ts_serialize('back\\slash "quote" tab\there')
    assert s.startswith('"') and s.endswith('"')
    assert "\\\\" in s   # backslash escaped
    assert '\\"' in s    # double-quote escaped
    assert "\\t" in s    # tab escaped


def test_assemble_splices_newest_first_and_length_plus_one():
    content, violations = assemble_ts_posts(_good_post(), "2026-07-25", CFG, "tok",
                                            fetch=_fetch_fixture)
    assert violations == [], violations
    # length +1: the fixture had 2 top-level posts, the result has 3
    assert FIXTURE.count('slug: "') == 2
    assert content.count('slug: "') == 3
    # unique slug, present exactly once
    assert content.count('slug: "brand-new-post"') == 1
    # newest-first: the new post is spliced BEFORE the previously-first one
    assert content.index("brand-new-post") < content.index("existing-one")
    # anchor + tail preserved intact
    assert content.count("export const POSTS: Post[] = [") == 1
    assert "export const getPost" in content
    # derived hero image path matches the slug (like the MDX path derives it)
    assert '"/blog/brand-new-post-hero.png"' in content


def test_assemble_refuses_duplicate_slug():
    content, violations = assemble_ts_posts(_good_post("existing-one"), "2026-07-25",
                                            CFG, "tok", fetch=_fetch_fixture)
    assert content == ""
    assert any(("exist" in v.lower() or "dup" in v.lower()) for v in violations)


def test_serialized_literal_parses_as_real_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for the JS parse-check")
    content, violations = assemble_ts_posts(_good_post(), "2026-07-25", CFG, "tok",
                                            fetch=_fetch_fixture)
    assert violations == []
    # Recover exactly the inserted literal (spliced right after the anchor line).
    anchor = "export const POSTS: Post[] = ["
    insert_at = FIXTURE.index("\n", FIXTURE.index(anchor)) + 1
    end = insert_at + (len(content) - len(FIXTURE))
    literal = content[insert_at:end].rstrip().rstrip(",")
    script = ("const o = (" + literal + ");"
              "process.stdout.write([o.slug, String(o.body.length), o.title]"
              ".join(String.fromCharCode(1)));")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    slug, blen, title = out.stdout.split(SEP)
    assert slug == "brand-new-post"
    assert blen == "7"
    # special chars round-tripped through node's parser intact
    assert 'Quotes "inside"' in title
    assert "backslash \\" in title
    assert "$var" in title
    assert "`tick`" in title
