"""services/slipstream_repair.py -- the TARGETED REPAIR pass for Slipstream.

Where it sits: slipstream_engine._factcheck_stage runs the gate, one writer
revision, the gate again. Across nine held dry runs on 2026-11-15 the post that
came out of that was close: one to six sentences still failed (a claim that
went past its quote, an unsourced "most / usually" line, an editor style item).
This pass fixes exactly those sentences, once, and then the full gate runs one
final time. No loop.

The contract, and CODE enforces it, not the prompt:

  * Input to the model is only the failing sentences: the raw text of the
    field each sits in, the exact violation, and the pack quote(s) it cites.
  * For each failing sentence the model may (a) rewrite it to say no more than
    the cited quote says, or (b) delete it. Nothing else.
  * After the edit, code diffs the repaired post against the revised one,
    sentence by sentence, and REJECTS the repair (the post is HELD) if:
      - any sentence that passed was changed, removed or reordered,
      - a sentence was added,
      - a figure or a URL appears that was not in the sentence it replaces,
      - a banned-content hit was reworded instead of deleted,
      - copies of the same claim (body, FAQ, social) were not treated alike,
      - a deletion left a heading with no body, an empty component, an FAQ
        question with no answer, an empty description or social draft,
      - the post fell under its minimum length, or the format gate fails.
  * A post with too many failing sentences is not repaired at all: it HOLDS.

Everything is a pure function with an injected `llm`, so the pass is fully
unit-testable offline. `repair_post` never raises.
"""
from __future__ import annotations

import copy
import difflib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from services import slipstream_factcheck as fc

logger = logging.getLogger(__name__)

# Editor items a sentence-level edit can fix. The other two (answers_title,
# concrete_specific) are about something the post LACKS; repair may not add.
REPAIRABLE_EDITOR = {"no_filler_or_hype", "no_unsupported_absolutes", "no_invented_timelines",
                     "no_em_dashes", "operator_voice"}
_GROUP_OVERLAP = 0.6
_MAX_QUOTES_PER_UNIT = 8


class RepairError(Exception):
    """The repair pass cannot run on this post. Always becomes a HOLD."""


# ----------------------------------------------------------------------------
# Containers: the addressable raw text fields of a post
# ----------------------------------------------------------------------------

@dataclass
class Container:
    cid: str
    path: Tuple[Any, ...]           # where the raw text lives in the post dict
    raw: str
    units: List[fc.Unit]
    editable: bool = True           # False: meta text the repair pass never touches
    delete_only: bool = False       # stat blocks, table rows: cannot be reworded


def _mk(path, raw, units, editable=True, delete_only=False) -> Container:
    return Container("", tuple(path), raw, list(units), editable, delete_only)


def _block_containers(i: int, b: Dict[str, Any]) -> List[Container]:
    """Fields of one block, in the SAME order fc.units_from_blocks emits units."""
    t = b.get("type")
    one = lambda blk: fc.units_from_blocks([blk])[0]      # noqa: E731
    out: List[Container] = []
    if t in ("answer", "p", "quote", "h2", "h3", "definition"):
        out.append(_mk(("body", i, "text"), str(b.get("text") or ""), one({"type": t, "text": b.get("text")})))
    elif t == "callout":
        out.append(_mk(("body", i, "title"), str(b.get("title") or ""),
                       one({"type": t, "title": b.get("title")})))
        out.append(_mk(("body", i, "text"), str(b.get("text") or ""), one({"type": t, "text": b.get("text")})))
    elif t == "ul":
        for j, item in enumerate(b.get("items") or []):
            out.append(_mk(("body", i, "items", j), str(item), one({"type": t, "items": [item]})))
    elif t == "table":
        for j, row in enumerate(b.get("rows") or []):
            out.append(_mk(("body", i, "rows", j), ", ".join(str(c) for c in row),
                           one({"type": t, "rows": [row]}), delete_only=True))
    elif t == "image":
        if b.get("alt"):
            out.append(_mk(("body", i, "alt"), str(b["alt"]), one({"type": t, "alt": b["alt"]}),
                           editable=False))
        out.append(_mk(("body", i, "caption"), str(b.get("caption") or ""),
                       one({"type": t, "caption": b.get("caption")})))
    elif t == "stat":
        out.append(_mk(("body", i), json.dumps(b, ensure_ascii=False), one(b), delete_only=True))
    elif t in ("sources", "links"):
        if b.get("title"):
            out.append(_mk(("body", i, "title"), str(b["title"]), one({"type": t, "title": b["title"]}),
                           editable=False))
        for j, item in enumerate(b.get("items") or []):
            out.append(_mk(("body", i, "items", j), json.dumps(item, ensure_ascii=False),
                           one({"type": t, "items": [item]}), editable=False))
    return out


def containers(post: Dict[str, Any]) -> List[Container]:
    """Every text field of the post with the sentence Units it yields, in the
    exact order fc.extract_post walks them. Raises RepairError if the two walks
    ever disagree: then no sentence can be safely addressed, and the post HOLDS."""
    out: List[Container] = []
    if post.get("title"):
        out.append(_mk(("title",), str(post["title"]),
                       [fc.Unit(text=fc._clean(str(post["title"])), where="title", surface="meta")],
                       editable=False))
    if post.get("description"):
        out.append(_mk(("description",), str(post["description"]),
                       fc._sentences(str(post["description"]), "description", "secondary")))
    if isinstance(post.get("body_mdx"), str):
        for i, line in enumerate(post["body_mdx"].splitlines()):
            out.append(_mk(("body_mdx", i), line, fc.units_from_mdx(line)))
    elif isinstance(post.get("body"), list):
        for i, b in enumerate(post["body"]):
            if isinstance(b, dict):
                out.extend(_block_containers(i, b))
    else:
        raise RepairError("post has neither a body_mdx string nor a body block array")
    if post.get("heroAlt"):
        out.append(_mk(("heroAlt",), str(post["heroAlt"]),
                       [fc.Unit(text=fc._clean(str(post["heroAlt"])), where="hero alt", surface="meta")],
                       editable=False))
    for i, qa in enumerate(post.get("faq") or []):
        if isinstance(qa, dict):
            out.append(_mk(("faq", i, "q"), str(qa.get("q") or ""),
                           fc._sentences(str(qa.get("q") or ""), "faq question", "meta"), editable=False))
            out.append(_mk(("faq", i, "a"), str(qa.get("a") or ""),
                           fc._sentences(str(qa.get("a") or ""), "faq answer", "secondary")))
    social = post.get("social") or {}
    if isinstance(social, dict):
        for k, v in social.items():
            out.append(_mk(("social", k), str(v or ""), fc._sentences(str(v or ""), f"social {k}", "secondary")))
    units, _ = fc.extract_post(post)
    flat = [u for c in out for u in c.units]
    if [(u.text, u.links, u.where) for u in flat] != [(u.text, u.links, u.where) for u in units]:
        raise RepairError("could not map the post's sentences back to its text fields")
    n = 0
    for idx, c in enumerate(out, 1):
        c.cid = f"C{idx}"
        for u in c.units:
            n += 1
            u.uid = f"S{n}"
    return out


# ----------------------------------------------------------------------------
# Planning: which sentences, and is the post repairable at all
# ----------------------------------------------------------------------------

def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def group_copies(units: List[fc.Unit]) -> List[List[str]]:
    """Failing sentences that say the same thing in different places (body,
    FAQ, social draft). They must get the same fix. Same figures plus shared
    wording, or heavy word overlap, puts two sentences in one group."""
    groups: List[List[fc.Unit]] = []
    for u in units:
        words = _content_words(u.text)
        figs = {f.key for f in fc.extract_figures(u.text)}
        home = None
        for g in groups:
            w0 = _content_words(g[0].text)
            f0 = {f.key for f in fc.extract_figures(g[0].text)}
            overlap = len(words & w0) / max(1, min(len(words), len(w0)))
            if overlap >= _GROUP_OVERLAP or (figs and figs == f0 and overlap >= 0.3):
                home = g
                break
        if home is None:
            groups.append([u])
        else:
            home.append(u)
    return [[u.uid for u in g] for g in groups if len(g) > 1]


def plan(failures: List[Dict[str, Any]], conts: List[Container], claims_judged: int,
         max_fail_ratio: float) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
    """({uid: [failures]}, skip_reason). skip_reason is set when the pass must
    not run: nothing addressable, a failure no sentence edit can fix, or too
    many failing sentences."""
    if not failures:
        return {}, "the gate reported no sentence-level failures to repair"
    by_uid = {u.uid: (u, c) for c in conts for u in c.units}
    targets: Dict[str, List[Dict[str, Any]]] = {}
    for f in failures:
        rule = str(f.get("rule") or "")
        if rule.startswith("editor:") and rule.split(":", 1)[1] not in REPAIRABLE_EDITOR:
            return {}, (f"the editor failed '{rule.split(':', 1)[1]}', which is about what the post "
                        "lacks; a repair may not add anything")
        hit = by_uid.get(f.get("uid") or "")
        if hit is None or hit[0].text != f.get("text"):
            return {}, f"a failure could not be tied to one sentence ({rule}): {fc._short(f.get('violation', ''))}"
        unit, cont = hit
        if not cont.editable:
            return {}, (f"a failure sits in the {unit.where}, which the repair pass does not edit: "
                        f"{fc._short(f.get('violation', ''))}")
        targets.setdefault(unit.uid, []).append(f)
    # "More than a third of the claims". When no claim was judged (the model
    # passes were skipped because a code check already failed), measure against
    # the post's sentences instead.
    judged = int(claims_judged or 0)
    base = judged or sum(1 for c in conts for u in c.units if u.surface != "meta") or 1
    if len(targets) / base > max_fail_ratio:
        of = f"{judged} judged claims" if judged else f"{base} sentences"
        return {}, (f"{len(targets)} failing sentences against {of} is more than the repair limit "
                    f"({max_fail_ratio:.0%}); a post this far off is held, not patched")
    return targets, None


# ----------------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------------

REPAIR_SYSTEM = (
    "You repair single sentences in a blog post that a fact-check gate has held. You are not "
    "rewriting the post. You get a few FIELDS of raw post text. In each, one or more sentences "
    "FAILED. For every failing sentence do exactly ONE of two things:\n"
    "(a) REWRITE it so it says no more than the QUOTE listed for it says, in plain words, or so "
    "it no longer breaks the style rule named;\n"
    "(b) DELETE it.\n"
    "Deleting is always acceptable and is the right choice when no quote supports the sentence.\n\n"
    "HARD LIMITS. A program compares your output with the original, sentence by sentence, and "
    "throws the whole repair away if you break one:\n"
    "- Leave every sentence that is not listed as failing EXACTLY as it is, character for "
    "character, including its markup, tags and links.\n"
    "- Add no sentence. Add no new claim, number, duration, link or URL. A rewrite may only use "
    "figures and links the failing sentence already had.\n"
    "- A sentence marked DELETE ONLY must be deleted, not reworded.\n"
    "- Sentences in the same GROUP say the same thing in different places. Give them the same "
    "fix: all deleted, or all rewritten to the same statement.\n"
    "- Keep the field's markup valid: keep tags closed and keep a link's markdown intact. If "
    "removing a sentence would leave a tag or a list item empty, return an empty string for "
    "that field.\n"
    "- No em-dashes.\n\n"
    "Return ONLY a JSON object, no prose and no code fence. One entry per FIELD you were given, "
    "with the field's full new raw text (an empty string deletes the whole field):\n"
    '{"fields": [{"id": "C7", "text": "the full repaired raw text of that field"}]}'
)


def _quotes_for(unit: fc.Unit, pack: Optional[Dict[str, Any]], domain: str,
                post_level: List[str]) -> List[str]:
    """Pack quotes the sentence is entitled to: those of the sources it links
    (or, in a block format, of the sources the post lists), plus first-party
    quotes of a linked brand page. Second-hand figures are never offered."""
    if not pack:
        return []
    from services.slipstream_research import _blocked_second_hand, canonical_url
    hrefs = list(unit.links) or list(post_level)
    keys = set()
    for h in hrefs:
        r = fc.resolve_link(h, domain)
        if r:
            keys.add(canonical_url(r[0]))
    out: List[str] = []
    for s in pack.get("sources") or []:
        if canonical_url(s["url"]) in keys:
            out.extend(q["text"] for q in s.get("quotes") or [] if not _blocked_second_hand(q))
    for b in pack.get("brand_pages") or []:
        if canonical_url(b["url"]) in keys:
            out.extend(q["text"] for q in b.get("quotes") or [])
    return out[:_MAX_QUOTES_PER_UNIT]


def sole_body_fields(conts: List[Container], targets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    """{field id: heading text} for failing fields in a section where EVERY
    body field is failing. Deleting them all would leave the heading with no
    body, which the contract rejects, so the model has to be told up front.
    (First live repair, 2026-11-19: both paragraphs under the closing heading
    failed, the model deleted both, and the whole repair was thrown away.)"""
    def index(c: Container) -> Tuple[str, int]:
        return (str(c.path[0]), c.path[1]) if c.path[0] in ("body_mdx", "body") and len(c.path) > 1 else ("", -1)

    sections: List[Tuple[str, List[Container]]] = []
    for c in conts:
        kind, _i = index(c)
        if not kind or not c.units:
            continue
        if all(u.where == "heading" for u in c.units):
            sections.append((c.units[0].text, []))
        elif sections and c.editable and any(u.surface == "body" for u in c.units) \
                and not all(u.where == "image caption" for u in c.units):
            sections[-1][1].append(c)
    out: Dict[str, str] = {}
    for heading, body in sections:
        if body and all(all(u.uid in targets for u in c.units) for c in body):
            for c in body:
                out[c.cid] = heading
    return out


def build_repair_prompt(conts: List[Container], targets: Dict[str, List[Dict[str, Any]]],
                        groups: List[List[str]], pack: Optional[Dict[str, Any]], domain: str,
                        post_level: List[str]) -> str:
    group_of = {uid: f"G{i}" for i, g in enumerate(groups, 1) for uid in g}
    sole = sole_body_fields(conts, targets)
    lines = ["Repair ONLY the failing sentences in these fields.", ""]
    for c in conts:
        failing = [u for u in c.units if u.uid in targets]
        if not failing:
            continue
        lines.append(f"=== FIELD {c.cid} ({failing[0].where}) ===")
        if c.cid in sole:
            lines.append(f'SECTION WARNING: every sentence under the heading "{sole[c.cid]}" is failing. '
                         "If you delete them all, the heading is left with no body and the whole repair "
                         "is thrown away. REWRITE at least one sentence of this section instead of deleting it.")
        lines.append("RAW TEXT:")
        lines.append(c.raw)
        for u in failing:
            fails = targets[u.uid]
            delete_only = c.delete_only or any(f["rule"] == "banned" for f in fails)
            tag = f" GROUP {group_of[u.uid]}" if u.uid in group_of else ""
            lines.append(f"FAILING SENTENCE {u.uid}{tag}{' DELETE ONLY' if delete_only else ''}: {u.text}")
            for f in fails:
                lines.append(f"  VIOLATION ({f['rule']}): {f['violation']}")
            if not delete_only:
                quotes = _quotes_for(u, pack, domain, post_level)
                for q in quotes:
                    lines.append(f'  QUOTE: "{q}"')
                if not quotes:
                    lines.append("  QUOTE: none available. Delete it, or rewrite it as plain advice with "
                                 "no factual claim.")
        lines.append("")
    return "\n".join(lines)


def default_llm() -> Callable[[str, str], Dict[str, Any]]:
    """The writer model, on the writer's transport and ledger. A small call:
    only the failing fields go in."""
    from services import slipstream_generate as gen

    def _call(system: str, user: str) -> Dict[str, Any]:
        return gen._llm_json_once(system, user, max_tokens=12000, timeout=300,
                                  surface="slipstream_repair")
    return _call


# ----------------------------------------------------------------------------
# Applying the edit
# ----------------------------------------------------------------------------

def _set(post: Dict[str, Any], path: Tuple[Any, ...], value: Any) -> None:
    node: Any = post
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


_DROP = object()


def apply_fields(post: Dict[str, Any], conts: List[Container], fields: Dict[str, str]) -> Dict[str, Any]:
    """Write the repaired raw text of each field into a COPY of the post, then
    drop what an empty string deleted (an MDX line, a paragraph block, a list
    item, a table row, a stat block)."""
    new = copy.deepcopy(post)
    by_cid = {c.cid: c for c in conts}
    mdx_lines = new["body_mdx"].splitlines() if isinstance(new.get("body_mdx"), str) else None
    drop_blocks: set = set()
    for cid, text in fields.items():
        c = by_cid[cid]
        text = text if isinstance(text, str) else ""
        if c.delete_only and text.strip():
            raise RepairError(f"field {cid} is delete-only (a stat or a table row) and was reworded")
        p = c.path
        if p[0] == "body_mdx":
            mdx_lines[p[1]] = text if text.strip() else _DROP
        elif p[0] == "body":
            if len(p) == 2:                                   # a whole stat block
                drop_blocks.add(p[1])
            elif p[2] in ("items", "rows"):
                new["body"][p[1]][p[2]][p[3]] = text if (text.strip() and p[2] == "items") else _DROP
            else:
                new["body"][p[1]][p[2]] = text.strip()
        else:
            _set(new, p, text.strip())
    if mdx_lines is not None:
        kept = [ln for ln in mdx_lines if ln is not _DROP]
        new["body_mdx"] = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    if isinstance(new.get("body"), list):
        body = []
        for i, b in enumerate(new["body"]):
            if i in drop_blocks or not isinstance(b, dict):
                continue
            t = b.get("type")
            for key in ("items", "rows"):
                if isinstance(b.get(key), list):
                    b[key] = [x for x in b[key] if x is not _DROP]
            if t in ("p", "quote", "h2", "h3", "definition") and not str(b.get("text") or "").strip():
                continue
            if t == "callout" and not str(b.get("text") or "").strip():
                continue
            if (t == "ul" and not b.get("items")) or (t == "table" and not b.get("rows")):
                continue
            body.append(b)
        new["body"] = body
    return new


# ----------------------------------------------------------------------------
# The contract (code, not the prompt)
# ----------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>\"')\]\\]+")
_BODY_BLOCKS = ("p", "ul", "table", "callout", "quote", "definition", "stat", "answer")


def _urls(post: Dict[str, Any]) -> set:
    return {u.rstrip(".,;:") for u in _URL_RE.findall(json.dumps(post, ensure_ascii=False))}


def _body_words(post: Dict[str, Any]) -> int:
    units, _ = fc.extract_post(post)
    return sum(len(u.text.split()) for u in units if u.surface == "body")


def structure_problems(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """What a deletion can break. Only problems the repair INTRODUCED count: a
    flaw the revised post already had is the format gate's business."""
    def scan(post: Dict[str, Any]) -> set:
        out = set()
        if not str(post.get("description") or "").strip():
            out.add("the description is empty")
        for i, qa in enumerate(post.get("faq") or []):
            if isinstance(qa, dict) and str(qa.get("q") or "").strip() and not str(qa.get("a") or "").strip():
                out.add(f"FAQ question {i + 1} has no answer: \"{fc._short(str(qa.get('q')))}\"")
        for k, v in (post.get("social") or {}).items() if isinstance(post.get("social"), dict) else []:
            if not str(v or "").strip():
                out.add(f"the {k} social draft is empty")
        if isinstance(post.get("body_mdx"), str):
            body = post["body_mdx"]
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            for i, ln in enumerate(lines):
                if ln.startswith("#") and (i + 1 == len(lines) or lines[i + 1].startswith("#")):
                    out.add(f"heading has no body under it: \"{fc._short(ln)}\"")
            for m in re.finditer(r"<([A-Z]\w*)\b[^>/]*>\s*</\1>", body):
                out.add(f"an emptied <{m.group(1)}> component was left in the body")
            if re.search(r"\[\s*\]\([^)]*\)", body):
                out.add("a link was left with no link text")
            if re.search(r"(?m)^\s*(?:[-*+]|\d+[.)])\s*$", body):
                out.add("an empty list item was left in the body")
        elif isinstance(post.get("body"), list):
            blocks = [b for b in post["body"] if isinstance(b, dict)]
            if not blocks or blocks[0].get("type") != "answer" or not str(blocks[0].get("text") or "").strip():
                out.add("the answer-first block is missing or empty")
            for i, b in enumerate(blocks):
                if b.get("type") in ("h2", "h3"):
                    nxt = next((x.get("type") for x in blocks[i + 1:] if x.get("type") != "image"), None)
                    if nxt is None or nxt in ("h2", "h3") or nxt not in _BODY_BLOCKS:
                        out.add(f"heading has no body under it: \"{fc._short(str(b.get('text')))}\"")
                if b.get("type") == "callout" and not str(b.get("text") or "").strip():
                    out.add("a callout was left empty")
        return out
    return [f"repair left the post broken: {p}" for p in sorted(scan(new) - scan(old))]


def check_contract(old: Dict[str, Any], new: Dict[str, Any], failing: Dict[str, List[Dict[str, Any]]],
                   groups: List[List[str]], min_words: int = 0) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Diff the repaired post against the revised one. Returns (violations,
    changes). Any violation means the repair is thrown away and the post HELD.
    `changes` is the reviewer's record: each sentence rewritten or deleted."""
    old_units, _ = fc.extract_post(old)
    new_units, _ = fc.extract_post(new)
    key = lambda u: (u.text, tuple(u.links), u.where, u.surface)      # noqa: E731
    banned = {uid for uid, fs in failing.items() if any(f["rule"] == "banned" for f in fs)}
    v: List[str] = []
    changes: List[Dict[str, Any]] = []
    status: Dict[str, str] = {}

    def figs(units: List[fc.Unit]) -> set:
        return {f.key for u in units for f in fc.extract_figures(u.text)}

    def rules(u: fc.Unit) -> List[str]:
        return sorted({f["rule"] for f in failing.get(u.uid, [])})

    sm = difflib.SequenceMatcher(None, [key(u) for u in old_units], [key(u) for u in new_units],
                                 autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        o, n = old_units[i1:i2], new_units[j1:j2]
        if tag == "equal":
            continue
        if tag == "insert":
            v += [f'repair added a sentence: "{fc._short(u.text)}"' for u in n]
            continue
        for u in o:
            if u.uid not in failing:
                v.append(f"repair {'removed' if tag == 'delete' else 'changed'} a sentence that had "
                         f'passed ({u.where}): "{fc._short(u.text)}"')
        if tag == "delete":
            for u in o:
                status[u.uid] = "deleted"
                changes.append({"action": "deleted", "where": u.where, "before": u.text, "after": "",
                                "rules": rules(u)})
            continue
        # replace
        if len(n) > len(o):
            v.append(f"repair turned {len(o)} sentence(s) into {len(n)}: "
                     f'"{fc._short(" ".join(u.text for u in n))}"')
        for u in o:
            if u.uid in banned:
                v.append("banned content may only be deleted, not reworded: "
                         f'"{fc._short(u.text)}"')
        new_figs = figs(n) - figs(o)
        if new_figs:
            raw = [f.raw for u in n for f in fc.extract_figures(u.text) if f.key in new_figs]
            v.append(f"repair introduced a figure the sentence did not have ({', '.join(dict.fromkeys(raw))}): "
                     f'"{fc._short(" ".join(u.text for u in n))}"')
        new_links = {h for u in n for h in u.links} - {h for u in o for h in u.links}
        if new_links:
            v.append(f"repair introduced a link the sentence did not have: {', '.join(sorted(new_links))}")
        for k, u in enumerate(o):
            after = n[k].text if k < len(n) else ""
            status[u.uid] = "rewritten" if after else "deleted"
            changes.append({"action": status[u.uid], "where": u.where, "before": u.text,
                            "after": after, "rules": rules(u)})
    extra_urls = _urls(new) - _urls(old)
    if extra_urls:
        v.append(f"repair introduced a URL that was not in the post: {', '.join(sorted(extra_urls))}")
    extra_figs = figs(new_units) - figs(old_units)
    if extra_figs and not any("introduced a figure" in x for x in v):
        v.append("repair introduced a figure that was not in the post")
    for g in groups:
        seen = {status.get(uid, "unchanged") for uid in g}
        if "deleted" in seen and len(seen) > 1:
            v.append("copies of the same claim were not repaired the same way (one deleted, another "
                     "kept): " + ", ".join(g))
    v += structure_problems(old, new)
    if min_words and _body_words(new) < min_words <= _body_words(old):
        v.append(f"repair left the post under its minimum length ({_body_words(new)} body words, "
                 f"{min_words} needed)")
    return fc._dedupe(v), changes


# ----------------------------------------------------------------------------
# The pass
# ----------------------------------------------------------------------------

def repair_post(post: Dict[str, Any], gate_result: Dict[str, Any], cfg: Dict[str, Any], *,
                pack: Optional[Dict[str, Any]] = None,
                extra_failures: Optional[List[Dict[str, Any]]] = None,
                llm: Optional[Callable[[str, str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """ONE targeted repair of the sentences the gate failed.

    Returns {"ran", "ok", "post", "skipped", "violations", "changes", "targets"}:
      skipped     why the pass did not run at all (the caller HOLDS as before)
      ok          the repair ran AND kept the contract; `post` is the repaired post
      violations  contract breaches (the caller HOLDS; `post` is the unrepaired one)
    Never raises."""
    out: Dict[str, Any] = {"ran": False, "ok": False, "post": post, "skipped": None,
                           "violations": [], "changes": [], "targets": 0}
    try:
        fcfg = (cfg or {}).get("factcheck") or {}
        failures = list(gate_result.get("failures") or []) + list(extra_failures or [])
        conts = containers(post)
        claims = (gate_result.get("counts") or {}).get("claims") or len(gate_result.get("claims") or [])
        targets, skip = plan(failures, conts, claims, float(fcfg.get("repair_max_fail_ratio") or 1 / 3))
        if skip:
            out["skipped"] = skip
            return out
        out["targets"] = len(targets)
        units = [u for c in conts for u in c.units]
        groups = group_copies([u for u in units if u.uid in targets])
        domain = (cfg or {}).get("domain", "")
        _, post_level = fc.extract_post(post)
        prompt = build_repair_prompt(conts, targets, groups, pack, domain, post_level)
        out["ran"] = True
        reply = (llm or default_llm())(REPAIR_SYSTEM, prompt)
        if not isinstance(reply, dict) or not isinstance(reply.get("fields"), list):
            raise RepairError("repair reply has no 'fields' list")
        sent = {c.cid for c in conts if any(u.uid in targets for u in c.units)}
        fields: Dict[str, str] = {}
        for f in reply["fields"]:
            if not isinstance(f, dict) or not isinstance(f.get("text"), str):
                raise RepairError("repair reply contains a malformed field")
            cid = str(f.get("id") or "").strip()
            if cid not in sent:
                raise RepairError(f"repair returned field {cid or '?'}, which was not sent for repair")
            fields[cid] = f["text"]
        if fc._EM_DASH in json.dumps(fields, ensure_ascii=False):
            raise RepairError("repair output contains an em-dash")
        repaired = apply_fields(post, conts, fields)
        violations, changes = check_contract(post, repaired, targets, groups,
                                             int(fcfg.get("repair_min_words") or 0))
        out["changes"] = changes
        if violations:
            out["violations"] = violations
            return out
        if not changes:
            out["violations"] = ["repair changed nothing"]
            return out
        out["ok"] = True
        out["post"] = repaired
        return out
    except Exception as e:  # kill-safe: a broken repair HOLDS, it never publishes
        if not isinstance(e, RepairError):
            logger.exception("[slipstream] repair pass errored; post will be HELD")
        out["violations"] = [f"repair pass failed (post HELD): {type(e).__name__}: {e}"[:300]]
        return out


def summarize(result: Optional[Dict[str, Any]], final_ok: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """The `repair` block of the factcheck receipt."""
    if result is None:
        return None
    return {"ran": bool(result.get("ran")), "applied": bool(result.get("ok")),
            "skipped": result.get("skipped"), "targets": result.get("targets", 0),
            "contract_violations": list(result.get("violations") or []),
            "changes": list(result.get("changes") or []),
            "final_gate_passed": final_ok}


def pr_section(summary: Optional[Dict[str, Any]]) -> str:
    """Markdown for the PR body: every sentence the repair pass rewrote or
    deleted, so the human reviewer sees exactly what was cut."""
    if not summary or not summary.get("applied"):
        return ""
    changes = summary.get("changes") or []
    cut = sum(1 for c in changes if c["action"] == "deleted")
    head = (f"\n\n### Repair pass\n\n"
            f"After the one writer revision still failed, {len(changes)} sentence(s) were repaired "
            f"under a code-enforced contract ({len(changes) - cut} rewritten, {cut} deleted). Nothing "
            f"else in the post changed.\n\n")
    rows = ["| Action | Where | Before | After | Rule |", "|---|---|---|---|---|"]
    for c in changes:
        rows.append(f"| {c['action']} | {fc._cell(c['where'])} | {_cell(c['before'])} | "
                    f"{_cell(c['after']) if c['after'] else '(deleted)'} | "
                    f"{fc._cell(', '.join(c.get('rules') or []))} |")
    return head + "\n".join(rows) + "\n"


def _cell(s: Any) -> str:
    return fc._short(str(s or ""), 300).replace("|", "\\|").replace("\n", " ") or "-"
