"""services/slipstream_assemble.py -- assemble the generated content into the
final MDX file and run it through the publish gate.

Keeps generation (LLM) and validation (rules) separate: this is the deterministic
glue that produces exactly what gets committed, then hands it to validate_post so
nothing publishes that would break the build or violate the brand rules.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from services.slipstream_validate import validate_post


def _quote(s: str) -> str:
    """Double-quote a YAML scalar, escaping backslashes and quotes. Required
    because titles/descriptions contain colons ('Signal vs. Noise: How...') which
    are invalid unquoted YAML and crash the frontmatter parse (build failure)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fm_value(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_quote(x) for x in v) + "]"
    return _quote(v)


def assemble_mdx(post: Dict[str, Any], date_str: str) -> Tuple[str, List[str]]:
    """Return (final_mdx, violations). The hero image path is derived from the
    slug so it always matches what generate_images will write."""
    slug = post["slug"]
    frontmatter = {
        "title": post["title"],
        "description": post["description"],
        "date": date_str,
        "author": "Michael Rodriguez",
        "heroImage": f"/blog/{slug}-hero.png",
        "ogTitle": post.get("ogTitle", post["title"]),
        "tags": post.get("tags", []),
    }
    fm_lines = "\n".join(f"{k}: {_fm_value(v)}" for k, v in frontmatter.items())
    mdx = f"---\n{fm_lines}\n---\n\n{post['body_mdx'].strip()}\n"
    return mdx, validate_post(mdx)
