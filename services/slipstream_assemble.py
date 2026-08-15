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


# ---------------------------------------------------------------------------
# tsx_post format (Book'd): render the block-array body to a per-post .tsx module
# that imports the site's existing Slipstream components, and splice its import +
# registry entry into src/lib/blog.ts. Same read-modify-write discipline as
# assemble_ts_posts (validate_blocks gate, idempotency guard, anchor splice), but
# for the TWO-FILE shape the bookd-marketing-site blog already uses. No site change.
# ---------------------------------------------------------------------------

_REGISTRY_ANCHOR = "const registry: PostEntry[] = ["


def _jsx_text(s: Any) -> str:
    """Render text as a JS-string EXPRESSION child: `{"..."}`. Wrapping in a JS
    string (not raw JSX text) means braces, angle brackets, quotes and ampersands
    in LLM copy can never break JSX compilation."""
    return "{" + _ts_str(s) + "}"


def _heading_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "section"


def _js_ident(slug: str) -> str:
    ident = re.sub(r"[^a-zA-Z0-9]", "_", str(slug))
    return "Post_" + ident  # prefix guarantees a valid, collision-safe identifier


def _reading_minutes(blocks: List[dict]) -> int:
    words = 0
    for b in blocks:
        if b.get("text"):
            words += len(str(b["text"]).split())
        for it in (b.get("items") or []):
            words += len(str(it).split()) if isinstance(it, str) else 0
        for row in (b.get("rows") or []):
            words += sum(len(str(c).split()) for c in row)
    return max(1, round(words / 200))


def _blocks_to_jsx(blocks: List[dict], ind: str = "      ") -> str:
    """Render the generator's typed block array into JSX using the components the
    bookd site exports from @/components/blog/Slipstream. All text goes through
    _jsx_text so it always compiles; StatRow/Sources props use json (valid TS)."""
    out: List[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "answer":
            out.append(f"{ind}<AnswerFirst>{_jsx_text(b.get('text', ''))}</AnswerFirst>")
        elif t == "p":
            out.append(f"{ind}<p>{_jsx_text(b.get('text', ''))}</p>")
        elif t == "h2":
            txt = b.get("text", "")
            out.append(f"{ind}<h2 id={{{_ts_str(_heading_id(txt))}}}>{_jsx_text(txt)}</h2>")
        elif t == "h3":
            out.append(f"{ind}<h3>{_jsx_text(b.get('text', ''))}</h3>")
        elif t == "ul":
            lis = "".join(f"<li>{_jsx_text(it)}</li>" for it in (b.get("items") or []))
            out.append(f"{ind}<ul>{lis}</ul>")
        elif t == "definition":
            out.append(f"{ind}<EntityDefinition term={{{_ts_str(b.get('term', ''))}}}>"
                       f"{_jsx_text(b.get('text', ''))}</EntityDefinition>")
        elif t == "callout":
            title = b.get("title")
            ta = f" title={{{_ts_str(title)}}}" if title else ""
            out.append(f"{ind}<Callout{ta}>{_jsx_text(b.get('text', ''))}</Callout>")
        elif t == "quote":
            out.append(f"{ind}<PullQuote>{_jsx_text(b.get('text', ''))}</PullQuote>")
        elif t == "image":
            src = str(b.get("src", "")).replace("/blog/", "/img/")
            cap = f" caption={{{_ts_str(b['caption'])}}}" if b.get("caption") else ""
            out.append(f"{ind}<InBodyImage src={{{_ts_str(src)}}} alt={{{_ts_str(b.get('alt', ''))}}}{cap} />")
        elif t == "links":
            lis = "".join(
                f"<li><Link href={{{_ts_str(it.get('href', ''))}}}>{_jsx_text(it.get('label', ''))}</Link></li>"
                for it in (b.get("items") or []))
            out.append(f"{ind}<ul>{lis}</ul>")
        elif t == "table":
            heads = "".join(f"<th>{_jsx_text(h)}</th>" for h in (b.get("headers") or []))
            rows = "".join("<tr>" + "".join(f"<td>{_jsx_text(c)}</td>" for c in row) + "</tr>"
                           for row in (b.get("rows") or []))
            out.append(f'{ind}<div className="table-wrap"><table><thead><tr>{heads}</tr>'
                       f"</thead><tbody>{rows}</tbody></table></div>")
        elif t == "stat":
            item = {"v": b.get("value", ""), "k": b.get("label", "")}
            if b.get("source"):
                item["src"] = b["source"]
            out.append(f"{ind}<StatRow items={{[{json.dumps(item)}]}} />")
        elif t == "sources":
            items = [{"label": it.get("label", ""), "url": it.get("href", "")}
                     for it in (b.get("items") or [])]
            out.append(f"{ind}<Sources items={{{json.dumps(items)}}} />")
    return "\n".join(out)


def _collect_sources(blocks: List[dict]) -> List[dict]:
    """PostMeta.sources feeds the JSON-LD citation. Gather every cited source: the
    dedicated `sources` block plus any `stat` block that carries a source+href."""
    seen: Dict[str, str] = {}
    for b in blocks:
        if b.get("type") == "sources":
            for it in (b.get("items") or []):
                if it.get("href"):
                    seen.setdefault(it["href"], it.get("label") or it["href"])
        elif b.get("type") == "stat" and b.get("href"):
            seen.setdefault(b["href"], b.get("source") or b["href"])
    return [{"label": lbl, "url": url} for url, lbl in seen.items()]


def _normalize_faq(faq: Any) -> List[List[str]]:
    """PostMeta.faq is [question, answer][]. Accept the generator's [[q,a]...] or
    [{q,a}/{question,answer}...] and normalize to tuples."""
    out: List[List[str]] = []
    for item in (faq or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append([str(item[0]), str(item[1])])
        elif isinstance(item, dict):
            q = item.get("q") or item.get("question")
            a = item.get("a") or item.get("answer")
            if q and a:
                out.append([str(q), str(a)])
    return out


def _build_post_meta(post: Dict[str, Any], date_str: str, blocks: List[dict]) -> Dict[str, Any]:
    slug = post["slug"]
    meta: Dict[str, Any] = {
        "slug": slug,
        "title": post["title"],
        "description": post["description"],
        "date": date_str,
        "updated": date_str,
        "readingMinutes": _reading_minutes(blocks),
        "hero": f"/img/{slug}-hero.png",
        "heroAlt": post.get("heroAlt") or post["title"],
        "category": post.get("category") or "The Compliance Desk",
        "tags": post.get("tags") or [],
        "faq": _normalize_faq(post.get("faq")),
        "sources": _collect_sources(blocks),
    }
    return meta


def _render_tsx_module(post: Dict[str, Any], meta: Dict[str, Any], blocks: List[dict]) -> str:
    """The full <slug>.tsx file: Slipstream component imports, the typed meta
    literal, and the Article component rendering the blocks."""
    imports = (
        'import Link from "next/link";\n'
        "import {\n"
        "  AnswerFirst,\n  KeyTakeaway,\n  StatRow,\n  PullQuote,\n  Callout,\n"
        "  EntityDefinition,\n  InBodyImage,\n  Sources,\n"
        '} from "@/components/blog/Slipstream";\n'
        'import type { PostMeta } from "@/lib/blog";\n'
    )
    meta_literal = "export const meta: PostMeta = " + json.dumps(meta, indent=2) + ";\n"
    body = _blocks_to_jsx(blocks)
    article = ("export default function Article() {\n"
               "  return (\n    <>\n" + body + "\n    </>\n  );\n}\n")
    return imports + "\n" + meta_literal + "\n" + article


def _splice_registry(current: str, slug: str) -> Tuple[str, List[str]]:
    """Add the new post's import + registry entry to src/lib/blog.ts. Idempotent:
    refuses if the slug is already registered."""
    marker = f'from "@/content/posts/{slug}"'
    if marker in current:
        return "", [f"slug '{slug}' already registered in blog.ts (idempotent guard)"]
    if _REGISTRY_ANCHOR not in current:
        return "", [f"anchor '{_REGISTRY_ANCHOR}' not found in blog.ts (cannot splice safely)"]
    ident = _js_ident(slug)
    import_line = f'import {ident}, {{ meta as meta_{ident} }} from "@/content/posts/{slug}";\n'
    # Insert the import right after the LAST existing content-post import (or, if
    # none, right before the first `export`), so imports stay grouped at the top.
    last = current.rfind('from "@/content/posts/')
    if last >= 0:
        ins = current.index("\n", last) + 1
        current = current[:ins] + import_line + current[ins:]
    else:
        ei = current.index("\nexport ")
        current = current[:ei + 1] + import_line + current[ei + 1:]
    entry = f"  {{ meta: meta_{ident}, Article: {ident} }},\n"
    idx = current.index(_REGISTRY_ANCHOR)
    at = current.index("\n", idx) + 1
    return current[:at] + entry + current[at:], []


def assemble_tsx_post(
    post: Dict[str, Any],
    date_str: str,
    cfg: Dict[str, Any],
    token: str,
    *,
    fetch_registry: Optional[Callable[[Dict[str, Any], str], str]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Return ({posts_path: tsx, registry_path: new_blog_ts}, violations) for the
    tsx_post format. HOLDS (empty dict + violations) on any block-gate or splice
    failure, so a bad post never reaches the commit."""
    slug = post["slug"]
    blocks = post.get("body") or []
    # Reuse the exact block gate WD's rail uses.
    violations = validate_blocks({"slug": slug, "title": post["title"],
                                  "description": post["description"], "body": blocks})
    if violations:
        return {}, violations

    meta = _build_post_meta(post, date_str, blocks)
    tsx = _render_tsx_module(post, meta, blocks)

    fetch = fetch_registry or (lambda c, t: _default_fetch_file(c["repo"], c["registry_file"], t))
    current = fetch(cfg, token)
    new_registry, rv = _splice_registry(current, slug)
    if rv:
        return {}, rv

    posts_path = f"{cfg['posts_dir']}/{slug}.tsx"
    return {posts_path: tsx, cfg["registry_file"]: new_registry}, []


def _default_fetch_file(repo: str, path: str, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"cannot read {path} from {repo}: {r.status_code}")
    return base64.b64decode(r.json()["content"]).decode("utf-8")
