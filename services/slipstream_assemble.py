"""services/slipstream_assemble.py -- assemble the generated content into the
final MDX file and run it through the publish gate.

Keeps generation (LLM) and validation (rules) separate: this is the deterministic
glue that produces exactly what gets committed, then hands it to validate_post so
nothing publishes that would break the build or violate the brand rules.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from services.slipstream_validate import validate_blocks, validate_post


# A ConsoleDiagram used as a PAIRED tag with a raw-JSON child:
# <ConsoleDiagram>{ ...json... }</ConsoleDiagram>. The (?<!/) guard skips the
# valid self-closing form. Captures the whole child so nested braces survive.
_CONSOLE_PAIRED = re.compile(r"<ConsoleDiagram\b[^>]*(?<!/)>(.*?)</ConsoleDiagram>", re.S)


def _loads_first_json(text: str) -> Any:
    """Parse the first complete JSON value ({...} or [...]) inside `text`, or
    None. Uses raw_decode so nested/balanced braces parse correctly."""
    candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not candidates:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[min(candidates):])
        return obj
    except Exception:
        return None


def _attr_safe(s: str) -> str:
    """Make a value safe inside a double-quoted JSX attribute: collapse
    whitespace and swap the double-quote for a single-quote (literal inside a
    double-quoted attribute)."""
    return " ".join(str(s).split()).replace('"', "'")


def _steps_to_pipe(steps: Any) -> str:
    """Coerce a steps value (list, or a string already using '|'/newlines) into
    the pipe-delimited string the ConsoleDiagram component expects."""
    if isinstance(steps, (list, tuple)):
        parts = [str(s).strip() for s in steps]
    elif isinstance(steps, str):
        parts = [p.strip() for p in re.split(r"[|\n]", steps)]
    else:
        parts = []
    return " | ".join(p for p in parts if p)


def normalize_console_diagram(body: str) -> str:
    """Rewrite the build-breaking <ConsoleDiagram>{...raw JSON...}</ConsoleDiagram>
    form into the valid self-closing
    <ConsoleDiagram steps="a | b | c" caption="..." /> form.

    In MDX a bare '{' child is parsed as a JS expression and crashes compilation
    (the AvI/BAE build break). The ConsoleDiagram component only reads the `steps`
    (pipe string) and `caption` props, so we recover those from the JSON payload.
    If no steps can be recovered, we DROP the element (a missing optional diagram
    is safe; a bare brace is not) and let the gate HOLD if that leaves no visual.
    A paired form with plain-text (no-brace) children is left untouched -- it does
    not crash the build.
    """
    def _repl(m: "re.Match") -> str:
        child = m.group(1).strip()
        if "{" not in child and "[" not in child:
            return m.group(0)  # no bare expression -> not the crash case
        data = _loads_first_json(child)
        steps_pipe, caption = "", ""
        if isinstance(data, dict):
            steps_pipe = _steps_to_pipe(data.get("steps") or data.get("items") or data.get("nodes"))
            caption = str(data.get("caption") or "").strip()
        elif isinstance(data, list):
            steps_pipe = _steps_to_pipe(data)
        if not steps_pipe:
            return ""  # unrecoverable -> drop rather than ship a bare brace
        cap = f' caption="{_attr_safe(caption)}"' if caption else ""
        return f'<ConsoleDiagram steps="{_attr_safe(steps_pipe)}"{cap} />'

    return _CONSOLE_PAIRED.sub(_repl, body)


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
    # Normalize the ConsoleDiagram raw-JSON-child form the LLM sometimes emits
    # into valid self-closing MDX before the gate sees it (bare '{' crashes build).
    body = normalize_console_diagram(post["body_mdx"].strip())
    mdx = f"---\n{fm_lines}\n---\n\n{body}\n"
    return mdx, validate_post(mdx)


# ---------------------------------------------------------------------------
# ts_posts_array format (Worship Digital): serialize a Post into a TS object
# literal and splice it, newest-first, into the brand's src/content/posts.ts.
# ---------------------------------------------------------------------------

_IND = "  "  # two-space indent, matching the existing posts.ts
_ANCHOR = "export const POSTS: Post[] = ["


def _ts_str(s: Any) -> str:
    """Serialize a Python string as a valid DOUBLE-quoted TS string. We escape the
    backslash, the double-quote, and control chars. Backtick and `$` are literal
    inside a double-quoted string (they only matter in template literals, which we
    never emit) so they pass through unchanged -- escaping them would corrupt the
    content."""
    out = (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return '"' + out + '"'


def _ts_serialize(value: Any, level: int = 0) -> str:
    """Serialize a JSON-like Python value (str / bool / number / list / dict) into a
    valid, readable TS literal. Dict keys are emitted as bare identifiers (every key
    we produce is a valid TS identifier). Nested structures are indented two spaces
    per level, with trailing commas (matching the existing file)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        return _ts_str(value)

    pad = _IND * (level + 1)
    close = _IND * level
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = [pad + _ts_serialize(v, level + 1) for v in value]
        return "[\n" + ",\n".join(items) + "\n" + close + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}{k}: {_ts_serialize(v, level + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + close + "}"
    raise TypeError(f"cannot serialize {type(value).__name__} to a TS literal")


def _build_post_object(post: Dict[str, Any], date_str: str) -> "dict":
    """Map the generated post onto the WD `Post` type shape, in Post-type field
    order. heroImage is DERIVED from the slug so it always matches what
    generate_images writes (same discipline as the MDX path)."""
    slug = post["slug"]
    obj: Dict[str, Any] = {
        "slug": slug,
        "title": post["title"],
        "description": post["description"],
        "date": date_str,
        "category": post.get("category") or "Marketing",
        "heroImage": f"/blog/{slug}-hero.png",
        "heroAlt": post.get("heroAlt") or post["title"],
        "ogTitle": post.get("ogTitle") or post["title"],
    }
    if post.get("faq"):
        obj["faq"] = post["faq"]
    obj["body"] = post.get("body")
    return obj


def _default_fetch_posts(cfg: Dict[str, Any], token: str) -> str:
    """GET the current posts.ts from the brand repo via the GitHub Contents API."""
    repo = cfg["repo"]
    path = cfg["posts_file"]
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"cannot read {path} from {repo}: {r.status_code}")
    return base64.b64decode(r.json()["content"]).decode("utf-8")


def assemble_ts_posts(
    post: Dict[str, Any],
    date_str: str,
    cfg: Dict[str, Any],
    token: str,
    *,
    fetch: Callable[[Dict[str, Any], str], str] = _default_fetch_posts,
) -> Tuple[str, List[str]]:
    """Return (full_new_posts_ts, violations) for the ts_posts_array format.

    1. Build the Post object and run it through the block gate (validate_blocks).
       On any violation, return ("", violations) so the engine HOLDS -- a bad post
       never reaches the splice or the commit.
    2. GET the current posts.ts, guard against a duplicate slug (idempotent), then
       splice the serialized literal in right after the `export const POSTS` line
       (newest-first, matching the existing ordering).
    """
    post_obj = _build_post_object(post, date_str)
    violations = validate_blocks(post_obj)
    if violations:
        return "", violations

    slug = post_obj["slug"]
    current = fetch(cfg, token)

    # Idempotency guard: never write a slug that already exists in the array.
    if f'slug: "{slug}"' in current:
        return "", [f"slug '{slug}' already exists in posts.ts (idempotent guard, refusing duplicate)"]
    if _ANCHOR not in current:
        return "", [f"anchor '{_ANCHOR}' not found in posts.ts (cannot splice safely)"]

    literal = _IND + _ts_serialize(post_obj, level=1) + ",\n"
    idx = current.index(_ANCHOR)
    insert_at = current.index("\n", idx) + 1
    new_content = current[:insert_at] + literal + current[insert_at:]
    return new_content, []
