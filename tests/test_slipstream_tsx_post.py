"""tests/test_slipstream_tsx_post.py — the tsx_post rail (Book'd): render the
generator's block array into a per-post .tsx module + splice src/lib/blog.ts."""
from __future__ import annotations

import unittest
from unittest import mock

from services.slipstream_assemble import (
    _blocks_to_jsx, _build_post_meta, _splice_registry, _reading_minutes,
    assemble_tsx_post,
)


class TestBlocksToJsx(unittest.TestCase):
    def test_each_block_type_maps_to_its_component(self):
        j = _blocks_to_jsx([
            {"type": "answer", "text": "A"},
            {"type": "p", "text": "P"},
            {"type": "h2", "text": "A Question?"},
            {"type": "h3", "text": "Sub"},
            {"type": "definition", "term": "T", "text": "D"},
            {"type": "callout", "title": "C", "text": "CO"},
            {"type": "quote", "text": "Q"},
            {"type": "stat", "value": "$5", "label": "L", "source": "S", "href": "h"},
            {"type": "sources", "items": [{"label": "L", "href": "u"}]},
            {"type": "image", "src": "/blog/x-a.png", "alt": "alt"},
            {"type": "ul", "items": ["one", "two"]},
            {"type": "table", "headers": ["H"], "rows": [["c"]]},
            {"type": "links", "items": [{"label": "L", "href": "/blog/y"}]},
        ])
        self.assertIn("<AnswerFirst>", j)
        self.assertIn('<EntityDefinition term={"T"}>', j)
        self.assertIn('<Callout title={"C"}>', j)
        self.assertIn("<PullQuote>", j)
        self.assertIn('<StatRow items={[{"v": "$5"', j)   # StatRow uses {v,k,src}
        self.assertIn('"src": "S"', j)
        self.assertIn("<Sources items=", j)
        self.assertIn('src={"/img/x-a.png"}', j)           # /blog/ rewritten to /img/
        self.assertIn('id={"a-question"}', j)              # heading id slugified
        self.assertIn("<Link href={", j)
        self.assertIn('<div className="table-wrap">', j)

    def test_adversarial_text_is_wrapped_as_js_string(self):
        # quotes, angle brackets and braces in copy must never reach the JSX parser
        j = _blocks_to_jsx([{"type": "p", "text": 'a " and <tag> and {brace}'}])
        self.assertIn('<p>{"a ', j)
        self.assertNotIn("<p>a ", j)          # never emitted as raw JSX text
        self.assertIn("\\\"", j)               # the double-quote is escaped in the JS string

    def test_reading_minutes(self):
        self.assertEqual(_reading_minutes([{"type": "p", "text": " ".join(["w"] * 400)}]), 2)


class TestMeta(unittest.TestCase):
    def test_hero_date_faq_and_sources_collected(self):
        blocks = [
            {"type": "stat", "value": "1", "label": "l", "source": "src-label", "href": "http://a"},
            {"type": "sources", "items": [{"label": "L2", "href": "http://b"}]},
        ]
        post = {"slug": "my-slug", "title": "T", "description": "D",
                "faq": [{"question": "q", "answer": "a"}]}   # dict form normalizes to tuple
        m = _build_post_meta(post, "2026-08-14", blocks)
        self.assertEqual(m["hero"], "/img/my-slug-hero.png")
        self.assertEqual(m["date"], "2026-08-14")
        self.assertEqual(m["updated"], "2026-08-14")
        self.assertEqual(m["faq"], [["q", "a"]])
        urls = {s["url"] for s in m["sources"]}
        self.assertEqual(urls, {"http://a", "http://b"})   # stat href + sources block


class TestSplice(unittest.TestCase):
    BLOG = (
        'import type { ComponentType } from "react";\n'
        'import A, { meta as aMeta } from "@/content/posts/aaa";\n'
        "\n"
        "export type PostMeta = { slug: string };\n"
        "type PostEntry = { meta: PostMeta; Article: ComponentType };\n"
        "const registry: PostEntry[] = [\n"
        "  { meta: aMeta, Article: A },\n"
        "];\n"
    )

    def test_splice_adds_import_and_registry_entry(self):
        new, v = _splice_registry(self.BLOG, "new-post")
        self.assertEqual(v, [])
        self.assertIn('import Post_new_post, { meta as meta_Post_new_post } from "@/content/posts/new-post";', new)
        self.assertIn("{ meta: meta_Post_new_post, Article: Post_new_post },", new)
        # import grouped with the other content imports (before the type export)
        self.assertLess(new.index('from "@/content/posts/new-post"'), new.index("export type PostMeta"))
        # entry is inside the registry array, right after the anchor
        self.assertLess(new.index("Article: Post_new_post"), new.index("Article: A"))

    def test_idempotent_refuses_duplicate_slug(self):
        once, _ = _splice_registry(self.BLOG, "new-post")
        _, v = _splice_registry(once, "new-post")
        self.assertTrue(v and "already registered" in v[0])


class TestAssembleEndToEnd(unittest.TestCase):
    CFG = {"repo": "salesdroid/bookd-marketing-site",
           "posts_dir": "src/content/posts", "registry_file": "src/lib/blog.ts"}
    BLOG = "const registry: PostEntry[] = [\n];\nexport const posts = registry;\n"

    def test_returns_the_two_files(self):
        post = {"slug": "z-post", "title": "T", "description": "D",
                "body": [{"type": "answer", "text": "hi"}, {"type": "p", "text": "body"}]}
        with mock.patch("services.slipstream_assemble.validate_blocks", return_value=[]):
            files, v = assemble_tsx_post(post, "2026-08-14", self.CFG, "tok",
                                         fetch_registry=lambda c, t: self.BLOG)
        self.assertEqual(v, [])
        self.assertIn("src/content/posts/z-post.tsx", files)
        self.assertIn("src/lib/blog.ts", files)
        tsx = files["src/content/posts/z-post.tsx"]
        self.assertIn("export default function Article()", tsx)
        self.assertIn("export const meta: PostMeta =", tsx)
        self.assertIn('from "@/components/blog/Slipstream"', tsx)

    def test_gate_violation_holds_with_no_files(self):
        with mock.patch("services.slipstream_assemble.validate_blocks", return_value=["bad block"]):
            files, v = assemble_tsx_post({"slug": "s", "title": "t", "description": "d", "body": []},
                                         "2026-08-14", self.CFG, "tok", fetch_registry=lambda c, t: "")
        self.assertEqual(files, {})
        self.assertEqual(v, ["bad block"])


if __name__ == "__main__":
    unittest.main()
