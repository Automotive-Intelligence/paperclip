"""services/slipstream_validate.py -- the Slipstream publish GATE, in pure Python.

The Railway Slipstream engine calls this before publishing. Any violation HOLDS
the post (no publish). This is deterministic and fully testable, unlike a
build-in-an-agentic-loop gate. It enforces the file-98 v2 + visual-system bars:
required frontmatter, the required MDX components, hero + >=2 in-body images,
no em-dashes, and the ConsoleDiagram array-prop trap that crashes the build.
"""
from __future__ import annotations

import re
from typing import Any, List, Tuple

_REQUIRED_FRONTMATTER = ("title", "description", "date", "author")
_REQUIRED_COMPONENTS = ("AnswerFirst", "EntityDefinition", "PullQuote")

_EM_DASH = "—"  # the banned em-dash character, in every surface


def _split_frontmatter(mdx: str) -> Tuple[str, str]:
    """Return (frontmatter_block, body). Empty frontmatter if none."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", mdx, re.S)
    if not m:
        return "", mdx
    return m.group(1), m.group(2)


def validate_post(mdx: str) -> List[str]:
    """Return a list of violation strings; empty list means the post passes."""
    violations: List[str] = []
    fm, body = _split_frontmatter(mdx)

    # 1. Required frontmatter fields.
    for field in _REQUIRED_FRONTMATTER:
        if not re.search(rf"^{field}\s*:", fm, re.M):
            violations.append(f"missing required frontmatter field: {field}")

    # 2. No em-dashes anywhere (brand rule, all surfaces).
    if "—" in mdx:
        violations.append("em-dash present (banned in all copy)")

    # 3. Required v2 components in the body.
    for comp in _REQUIRED_COMPONENTS:
        if f"<{comp}" not in body:
            violations.append(f"missing required component: <{comp}>")
    if "<Callout" not in body and "<ConsoleDiagram" not in body:
        violations.append("missing a <Callout> or <ConsoleDiagram> visual element")

    # 4. ConsoleDiagram steps must be a pipe-delimited STRING, never an array
    #    (an array literal arrives undefined via next-mdx-remote/rsc and crashes the build).
    if re.search(r"<ConsoleDiagram[^>]*steps=\{\[", body):
        violations.append("ConsoleDiagram steps is an array literal (crashes the build); use a pipe string")

    # 4a. ConsoleDiagram must be SELF-CLOSING with a pipe-delimited steps string.
    #     A raw-JSON/brace child (<ConsoleDiagram>{...}</ConsoleDiagram>) is a bare
    #     '{' MDX expression that crashes compilation (AvI/BAE build break). assemble
    #     normalizes this away; this is the belt-and-suspenders gate for anything that
    #     slips through. The (?<!/) guard skips the valid self-closing form.
    if re.search(r"<ConsoleDiagram\b[^>]*(?<!/)>\s*[\{\[]", body):
        violations.append("ConsoleDiagram has a raw-JSON/brace child (crashes the build); use self-closing <ConsoleDiagram steps=\"a | b | c\" />")

    # 4b. Paired components must be CLOSED. An opened-but-unclosed <AnswerFirst> etc.
    #     is a JSX parse error that crashes the Vercel build (caught 2026-07-19).
    for comp in ("AnswerFirst", "PullQuote", "Callout", "EntityDefinition"):
        opens = len(re.findall(rf"<{comp}\b[^>]*>", body))
        selfclosed = len(re.findall(rf"<{comp}\b[^>]*/>", body))
        closes = len(re.findall(rf"</{comp}>", body))
        if (opens - selfclosed) != closes:
            violations.append(f"unbalanced <{comp}> tags (opened but not closed; breaks the build)")

    # 5. Visual system: a hero image in frontmatter + >=2 in-body images.
    if not re.search(r"^heroImage\s*:\s*\S+", fm, re.M):
        violations.append("missing heroImage in frontmatter (zero-image = auto-HOLD)")
    in_body_imgs = len(re.findall(r'<img\s[^>]*src="/blog/[^"]+"', body))
    if in_body_imgs < 2:
        violations.append(f"only {in_body_imgs} in-body image(s); Slipstream needs >=2")

    return violations


def _iter_strings(value: Any):
    """Yield every string reachable inside a Post/Block structure (for the
    em-dash scan across titles, block text, table cells, link labels, faq, etc.)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_strings(v)


def validate_blocks(post: dict) -> List[str]:
    """The publish GATE for the ts_posts_array format (Worship Digital), parallel
    to validate_post for MDX. `post` is the assembled Post object (heroImage set,
    body a list of WD Block dicts). Returns a list of violation strings; empty
    means it passes. Any violation HOLDS the post so a bad one never commits.

    Bars enforced: exactly one LEADING answer block; >=1 definition; >=1 quote;
    >=1 callout; heroImage set; >=2 image blocks; no em-dash anywhere.
    """
    violations: List[str] = []
    body = post.get("body")

    if not isinstance(body, list) or not body:
        violations.append("body is empty or not a Block array")
        # Without a body there is nothing more to check; still scan for em-dash.
        if _EM_DASH in "".join(_iter_strings(post)):
            violations.append("em-dash present (banned in all copy)")
        return violations

    types = [(b or {}).get("type") for b in body]

    # 1. Answer-first: exactly one answer block, and it must be first (AEO).
    if types[0] != "answer":
        violations.append("first body block must be an 'answer' block (answer-first / AEO)")
    n_answer = types.count("answer")
    if n_answer != 1:
        violations.append(f"expected exactly one 'answer' block, found {n_answer}")

    # 2. Required structural blocks.
    for req in ("definition", "quote", "callout"):
        if req not in types:
            violations.append(f"missing required block: {req}")

    # 3. Visual system: hero image set + >=2 in-body image blocks.
    if not post.get("heroImage"):
        violations.append("missing heroImage (zero-image = auto-HOLD)")
    n_images = types.count("image")
    if n_images < 2:
        violations.append(f"only {n_images} in-body image block(s); WD needs >=2")

    # 4. No em-dash anywhere (brand rule, every surface, nested included).
    if any(_EM_DASH in s for s in _iter_strings(post)):
        violations.append("em-dash present (banned in all copy)")

    return violations
