"""services/slipstream_validate.py -- the Slipstream publish GATE, in pure Python.

The Railway Slipstream engine calls this before publishing. Any violation HOLDS
the post (no publish). This is deterministic and fully testable, unlike a
build-in-an-agentic-loop gate. It enforces the file-98 v2 + visual-system bars:
required frontmatter, the required MDX components, hero + >=2 in-body images,
no em-dashes, and the ConsoleDiagram array-prop trap that crashes the build.
"""
from __future__ import annotations

import re
from typing import List, Tuple

_REQUIRED_FRONTMATTER = ("title", "description", "date", "author")
_REQUIRED_COMPONENTS = ("AnswerFirst", "EntityDefinition", "PullQuote")


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

    # 5. Visual system: a hero image in frontmatter + >=2 in-body images.
    if not re.search(r"^heroImage\s*:\s*\S+", fm, re.M):
        violations.append("missing heroImage in frontmatter (zero-image = auto-HOLD)")
    in_body_imgs = len(re.findall(r'<img\s[^>]*src="/blog/[^"]+"', body))
    if in_body_imgs < 2:
        violations.append(f"only {in_body_imgs} in-body image(s); Slipstream needs >=2")

    return violations
