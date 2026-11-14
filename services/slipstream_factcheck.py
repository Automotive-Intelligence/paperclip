"""services/slipstream_factcheck.py -- the Slipstream FACT-CHECK + EDITOR gate.

Runs in slipstream_engine.run_brand after the format gate (validate_post /
validate_blocks) passes, BEFORE any image is bought and before any PR is opened.
It returns violation strings in the same style as the format gate, so a failing
post is HELD with reasons.

Why it exists: the 2026-11-12 audit of three engine posts found dead source
links, links that did not say what the sentence beside them claimed, invented
timelines, product capabilities that are not live, and an anecdote invented in
the founder's voice. The format gate could not see any of that.

Four layers, cheapest first:

  1. Links (code)    every outbound link must resolve; every internal link must
                     return 200 on the brand's live domain.
  2. Figures (code)  every number / percentage / dollar amount must sit in a
                     sentence that carries a source link, and the figure must
                     literally appear in the fetched text of that source (or in
                     the brand facts file).
  3. Banned (code)   per-brand banned content from config/slipstream_brands.yaml
                     (factcheck: key): first-person experience, features that
                     are not live, off-brand framing.
  4. Verifier + editor (LLM, a DIFFERENT model than the writer)
                     claim-level SUPPORTED / NOT_SUPPORTED / UNVERIFIABLE with a
                     quoted passage that CODE confirms is really in the source,
                     then a binary editor rubric with quoted evidence.

Everything is a pure function with injected `http_get` and `llm` callables, so
the whole gate is unit-testable offline. `run_gate` never raises: if the gate
itself breaks (network down, model error, bad config) the post is HELD.
"""
from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A browser User-Agent: several authority sites (ftc.gov among them) answer a
# default python-requests agent with 403 or a fake 404.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
_FETCH_TIMEOUT_S = 20
_MAX_SOURCE_CHARS = 600_000        # kept per fetched page for the code checks
_PROMPT_SOURCE_CHARS = 30_000      # per source handed to the verifier model
_PROMPT_TOTAL_SOURCE_CHARS = 180_000
_MIN_QUOTE_CHARS = 20
_EM_DASH = chr(0x2014)

DEFAULT_VERIFIER_MODEL = "claude-opus-5-5"


class GateError(Exception):
    """The gate could not do its job (bad config, malformed model reply, model
    not distinct from the writer). Always converted into a HOLD by run_gate."""


class DnsFailure(Exception):
    """The host does not resolve. Counted as a DEAD link, not 'could not check'."""


# ----------------------------------------------------------------------------
# Config + brand facts
# ----------------------------------------------------------------------------

def resolve_factcheck_cfg(full_cfg: Dict[str, Any], brand_key: str) -> Dict[str, Any]:
    """Merge the top-level `factcheck:` block of slipstream_brands.yaml into one
    per-brand dict: defaults apply to every brand, brand entries add to them."""
    fc = (full_cfg or {}).get("factcheck") or {}
    defaults = fc.get("defaults") or {}
    brand = (fc.get("brands") or {}).get(brand_key) or {}
    return {
        "banned": list(defaults.get("banned") or []) + list(brand.get("banned") or []),
        "figure_allowlist": list(defaults.get("figure_allowlist") or [])
                            + list(brand.get("figure_allowlist") or []),
        "facts_file": brand.get("facts_file") or f"config/slipstream_facts/{brand_key}.md",
    }


def parse_facts(raw: str) -> str:
    """Return only the approved FACT lines of a facts file. HTML comments and
    markdown headings are documentation, not facts, so an empty starter file
    yields an empty string."""
    text = re.sub(r"<!--.*?-->", "", raw or "", flags=re.S)
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(re.sub(r"^[-*+]\s+", "", s))
    return "\n".join(lines)


def load_facts(cfg: Dict[str, Any]) -> str:
    """Approved first-party facts for a brand, or '' when none are on file."""
    fc = (cfg or {}).get("factcheck") or {}
    path = fc.get("facts_file") or f"config/slipstream_facts/{(cfg or {}).get('brand_key', '')}.md"
    if not os.path.isabs(path):
        path = os.path.join(_REPO_ROOT, path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_facts(fh.read())
    except OSError:
        return ""


def verifier_model() -> str:
    return (os.getenv("SLIPSTREAM_VERIFIER_MODEL") or "").strip() or DEFAULT_VERIFIER_MODEL


# ----------------------------------------------------------------------------
# Post -> sentences ("units") with the links each one carries
# ----------------------------------------------------------------------------

@dataclass
class Unit:
    text: str
    links: List[str] = field(default_factory=list)
    where: str = "body"
    # body      visible article text: figures need their own source link
    # secondary description / faq / social: figures must repeat a verified body figure
    # meta      title, alt text, link labels: banned-content scan only
    surface: str = "body"
    uid: str = ""


_MARK = "⟦{}⟧"                       # link placeholder that survives sentence splitting
_MARK_RE = re.compile(r"⟦(\d+)⟧")
_ABBREV = re.compile(r"\b(e\.g|i\.e|vs|etc|U\.S|Inc|Mr|Mrs|Ms|Dr|No|St|approx)\.", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9⟦])")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag) or re.search(rf"\b{name}\s*=\s*'([^']*)'", tag)
    return m.group(1) if m else ""


def _sentences(text: str, where: str, surface: str = "body", split: bool = True) -> List[Unit]:
    """Split a block of prose into sentence Units, keeping each link with the
    sentence it sits in. Handles <a href>, markdown links and bare URLs."""
    links: List[str] = []

    def _stash(url: str) -> str:
        links.append(url.rstrip(".,;:"))
        return _MARK.format(len(links) - 1)

    t = text or ""
    t = re.sub(r'<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
               lambda m: f"{m.group(2)} {_stash(m.group(1))}", t, flags=re.S | re.I)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                       # md images
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)", lambda m: f"{m.group(1)} {_stash(m.group(2))}", t)
    t = _URL_RE.sub(lambda m: _stash(m.group(0)), t)
    t = re.sub(r"<[^>]+>", " ", t)                                    # leftover tags
    t = re.sub(r"[*_`]{1,3}", "", t)
    t = _clean(t)
    if not t:
        return []
    t = _ABBREV.sub(lambda m: m.group(0)[:-1] + "․", t)          # protect "e.g." from the splitter
    parts = _SENT_SPLIT.split(t) if split else [t]
    out: List[Unit] = []
    for p in parts:
        ulinks = [links[int(i)] for i in _MARK_RE.findall(p)]
        plain = _clean(_MARK_RE.sub("", p).replace("․", "."))
        plain = re.sub(r"\(\s*\)", "", plain).strip()
        plain = re.sub(r"\s+([.,;:!?)])", r"\1", plain)
        if not plain and not ulinks:
            continue
        if not re.search(r"\w", plain) and out:
            out[-1].links.extend(ulinks)                              # a link trailing its sentence
            continue
        out.append(Unit(text=plain, links=ulinks, where=where, surface=surface))
    return out


def units_from_mdx(body: str) -> List[Unit]:
    """Sentence Units from an MDX body string (the default engine format)."""
    units: List[Unit] = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        for tag in re.findall(r"<img\b[^>]*>", line, flags=re.I):
            alt = _attr(tag, "alt")
            if alt:
                units.append(Unit(text=_clean(alt), where="image alt", surface="meta"))
        line = re.sub(r"<img\b[^>]*>", " ", line, flags=re.I)
        for tag in re.findall(r"<StatRow\b[^>]*/?>", line):
            text = _clean(f"{_attr(tag, 'value')} {_attr(tag, 'label')} ({_attr(tag, 'source')})")
            href = _attr(tag, "href")
            units.append(Unit(text=text, links=[href] if href else [], where="StatRow"))
        line = re.sub(r"<StatRow\b[^>]*/?>", " ", line)
        for tag in re.findall(r"<ConsoleDiagram\b[^>]*/?>", line):
            for step in (_attr(tag, "steps").split("|") + [_attr(tag, "caption")]):
                units.extend(_sentences(step, "diagram"))
        line = re.sub(r"<ConsoleDiagram\b[^>]*/?>", " ", line)
        if re.fullmatch(r"\|?[\s:|-]+\|?", line):                     # table separator row
            continue
        if line.startswith("|"):                                      # table row: one unit
            units.extend(_sentences(line.strip("|").replace("|", ", "), "table row", split=False))
            continue
        where = "heading" if line.startswith("#") else "body"
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)          # list markers
        units.extend(_sentences(line, where))
    return units


def units_from_blocks(body: List[Any]) -> Tuple[List[Unit], List[str]]:
    """Sentence Units from a structured block array (Worship Digital's
    ts_posts_array, plus shopify_article and tsx_post). Also returns the URLs of
    the post's `sources` blocks: in this shape prose cannot carry inline links,
    so a figure in a paragraph is checked against the post's listed sources."""
    units: List[Unit] = []
    post_sources: List[str] = []
    for b in body or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t in ("answer", "p", "quote", "h2", "h3"):
            units.extend(_sentences(str(b.get("text") or ""), "heading" if t in ("h2", "h3") else t))
        elif t == "definition":
            units.extend(_sentences(f"{b.get('text') or ''}", "definition"))
        elif t == "callout":
            units.extend(_sentences(str(b.get("title") or ""), "callout title"))
            units.extend(_sentences(str(b.get("text") or ""), "callout"))
        elif t == "ul":
            for item in b.get("items") or []:
                units.extend(_sentences(str(item), "list item"))
        elif t == "table":
            for row in b.get("rows") or []:
                units.extend(_sentences(", ".join(str(c) for c in row), "table row", split=False))
        elif t == "image":
            if b.get("alt"):
                units.append(Unit(text=_clean(str(b["alt"])), where="image alt", surface="meta"))
            units.extend(_sentences(str(b.get("caption") or ""), "image caption"))
        elif t == "stat":
            text = _clean(f"{b.get('value') or ''} {b.get('label') or ''} ({b.get('source') or ''})")
            href = str(b.get("href") or "").strip()
            units.append(Unit(text=text, links=[href] if href else [], where="stat"))
        elif t in ("sources", "links"):
            if b.get("title"):
                units.append(Unit(text=_clean(str(b["title"])), where=f"{t} title", surface="meta"))
            for item in b.get("items") or []:
                href = str((item or {}).get("href") or "").strip()
                units.append(Unit(text=_clean(str((item or {}).get("label") or "")),
                                  links=[href] if href else [], where=f"{t} block", surface="meta"))
                if t == "sources" and href:
                    post_sources.append(href)
    return units, post_sources


def extract_post(post: Dict[str, Any]) -> Tuple[List[Unit], List[str]]:
    """Every checkable string of a generated post, from EITHER shape (body_mdx
    string or body block array), as Units. Returns (units, post_sources)."""
    units: List[Unit] = []
    if post.get("title"):
        units.append(Unit(text=_clean(str(post["title"])), where="title", surface="meta"))
    if post.get("description"):
        units.extend(_sentences(str(post["description"]), "description", "secondary"))
    post_sources: List[str] = []
    if isinstance(post.get("body_mdx"), str):
        units.extend(units_from_mdx(post["body_mdx"]))
    elif isinstance(post.get("body"), list):
        bu, post_sources = units_from_blocks(post["body"])
        units.extend(bu)
    else:
        raise GateError("post has neither a body_mdx string nor a body block array")
    if post.get("heroAlt"):
        units.append(Unit(text=_clean(str(post["heroAlt"])), where="hero alt", surface="meta"))
    for qa in post.get("faq") or []:
        if isinstance(qa, dict):
            units.extend(_sentences(str(qa.get("q") or ""), "faq question", "meta"))
            units.extend(_sentences(str(qa.get("a") or ""), "faq answer", "secondary"))
    social = post.get("social") or {}
    if isinstance(social, dict):
        for k, v in social.items():
            units.extend(_sentences(str(v or ""), f"social {k}", "secondary"))
    for i, u in enumerate(units, 1):
        u.uid = f"S{i}"
    return units, post_sources


# ----------------------------------------------------------------------------
# Links
# ----------------------------------------------------------------------------

@dataclass
class Fetched:
    url: str
    state: str                  # live | dead | unchecked
    status: Optional[int] = None
    text: str = ""
    detail: str = ""
    internal: bool = False


def _host(u: str) -> str:
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def resolve_link(href: str, domain: str) -> Optional[Tuple[str, bool]]:
    """(absolute_url, is_internal), or None for a link the gate does not check
    (mailto:, tel:, in-page anchors)."""
    h = (href or "").strip()
    if not h or h.startswith(("#", "mailto:", "tel:", "sms:", "javascript:")):
        return None
    dom = (domain or "").lower()
    dom = dom[4:] if dom.startswith("www.") else dom
    if h.startswith("/") and not h.startswith("//"):
        return (f"https://{dom}{h}", True) if dom else None
    if h.startswith("//"):
        h = "https:" + h
    if not h.lower().startswith(("http://", "https://")):
        return None
    return h, bool(dom) and _host(h) == dom


def html_to_text(body: str) -> str:
    t = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>", " ", body or "")
    t = re.sub(r"(?s)<!--.*?-->", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return _clean(t)[:_MAX_SOURCE_CHARS]


def default_http_get(url: str):
    """The production fetch. `requests` on purpose (the service already uses it;
    ad hoc urllib TLS is unreliable). A host that does not resolve raises
    DnsFailure so it is counted DEAD rather than 'could not check'."""
    import requests
    try:
        return requests.get(url, timeout=_FETCH_TIMEOUT_S, allow_redirects=True,
                            headers={"User-Agent": BROWSER_UA,
                                     "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                     "Accept-Language": "en-US,en;q=0.9"})
    except requests.exceptions.ConnectionError as e:
        msg = str(e).lower()
        if any(s in msg for s in ("name or service not known", "nodename nor servname",
                                  "name resolution", "getaddrinfo failed", "no address associated")):
            raise DnsFailure(str(e)[:200])
        raise


def check_link(url: str, http_get: Callable[[str], Any], internal: bool = False) -> Fetched:
    """Resolve one link. 2xx = live. 404 / 410 / DNS failure = dead. Anything
    else (403, 429, 5xx, timeout) = could not check. One retry, so a single
    flaky response never decides the verdict."""
    last = Fetched(url=url, state="unchecked", detail="not attempted", internal=internal)
    for _attempt in range(2):
        try:
            r = http_get(url)
        except DnsFailure as e:
            last = Fetched(url=url, state="dead", detail=f"DNS failure: {e}"[:160], internal=internal)
            continue
        except Exception as e:  # timeout, TLS, connection reset: unknown, not dead
            last = Fetched(url=url, state="unchecked", detail=f"{type(e).__name__}: {e}"[:160],
                           internal=internal)
            continue
        status = int(getattr(r, "status_code", 0) or 0)
        if 200 <= status < 300:
            headers = getattr(r, "headers", None) or {}
            ctype = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
            raw = getattr(r, "text", "") or ""
            if "pdf" in ctype or raw.lstrip().startswith("%PDF"):
                return Fetched(url=url, state="live", status=status, text="",
                               detail="PDF: text not readable by the gate", internal=internal)
            return Fetched(url=url, state="live", status=status, text=html_to_text(raw),
                           internal=internal)
        state = "dead" if status in (404, 410) else "unchecked"
        last = Fetched(url=url, state=state, status=status, detail=f"HTTP {status}", internal=internal)
    if last.state == "dead" and last.status in (404, 410) and not internal:
        # Some sites answer every automated request with a fake 404 (ftc.gov did
        # this in the 2026-11-12 audit, for its own home page too). If the site
        # ROOT does not load either, the 404 says nothing about this page: it is
        # "could not check", not "dead". Both still HOLD; the reason differs.
        if not _root_loads(url, http_get):
            last = Fetched(url=url, state="unchecked", status=last.status, internal=internal,
                           detail=f"HTTP {last.status}, and the site root does not load either, "
                                  "so this host is likely blocking automated checks")
    return last


def _root_loads(url: str, http_get: Callable[[str], Any]) -> bool:
    p = urlparse(url)
    if not p.scheme or not p.netloc or p.path in ("", "/"):
        return True                 # the link IS the root: its own status stands
    try:
        r = http_get(f"{p.scheme}://{p.netloc}/")
        return 200 <= int(getattr(r, "status_code", 0) or 0) < 300
    except Exception:
        return False


def check_links(units: List[Unit], domain: str, http_get: Callable[[str], Any],
                cache: Optional[Dict[str, Fetched]] = None) -> Tuple[List[str], Dict[str, Fetched]]:
    """Check every link in the post once. Returns (violations, {href: Fetched}).
    The map is keyed by the href exactly as written in the post."""
    cache = cache if cache is not None else {}
    violations: List[str] = []
    by_href: Dict[str, Fetched] = {}
    for u in units:
        for href in u.links:
            if href in by_href:
                continue
            resolved = resolve_link(href, domain)
            if not resolved:
                continue
            url, internal = resolved
            if url not in cache:
                cache[url] = check_link(url, http_get, internal)
            f = by_href[href] = cache[url]
            if internal:
                if f.state != "live" or f.status != 200:
                    violations.append(f"internal link does not return 200 on {domain} "
                                      f"({f.detail or f.status}): {href}")
            elif f.state == "dead":
                violations.append(f"dead source link ({f.detail}): {href}")
            elif f.state == "unchecked":
                violations.append(f"could not check source link ({f.detail}): {href} "
                                  "(remove the claim it supports or cite a source that loads)")
    return violations, by_href


# ----------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Figure:
    raw: str
    kind: str                         # pct | usd | num | phrase
    value: Optional[Decimal] = None

    @property
    def key(self) -> Tuple[str, Any]:
        return (self.kind, self.value if self.kind != "phrase" else _norm(self.raw))


_NUM_RE = re.compile(r"""
    (?<![\w.,/])
    (?P<usd>\$\s?)?
    (?P<int>\d{1,3}(?:,\d{3})+|\d+)
    (?P<dec>\.\d+)?
    (?P<suf>\s?%|\s?(?:percent|per\s?cent)\b
        |\s?(?:thousand|million|billion|trillion)\b
        |[kKmMbB]\b|[xX]\b)?
    (?P<usd2>\s?(?:dollars|bucks|USD)\b)?
""", re.X | re.I)
_MULT = {"k": 3, "thousand": 3, "m": 6, "million": 6, "b": 9, "billion": 9, "trillion": 12}

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
          "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90,
          "hundred": 100, "thousand": 1000}
_W = "(?:" + "|".join(_WORDS) + ")"
_UNIT = (r"(?:business\s+)?(?:seconds?|minutes?|hours?|days?|weeks?|months?|quarters?|years?"
         r"|percent|times|reviews|customers|calls|leads|locations|stores|dealerships"
         r"|businesses|owners|clients|people|dollars)")
_WORDFIG_RE = re.compile(rf"\b({_W})((?:\s+(?:to|or|and)\s+{_W})?)[\s-]+{_UNIT}\b", re.I)
_LOOSE_RE = re.compile(r"\b(?:once or twice|(?:in|by) half|half (?:the|of|as)|twice as"
                       r"|(?:doubl|tripl|quadrupl)(?:e|es|ed|ing)(?![- ](?:book|check|click|sided|edged)))\b", re.I)


def _norm(s: str) -> str:
    """Normalization for literal containment checks: case, curly quotes, dash
    variants and whitespace never decide whether a passage 'appears'."""
    t = html.unescape(s or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[‐-―−]", "-", t)
    t = t.replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def extract_figures(text: str, allowlist: Iterable[str] = ()) -> List[Figure]:
    """Every number, percentage, dollar amount and spelled-out quantity in a
    sentence. Bare years (1900-2099) and allowlisted tokens (e.g. "24/7") are
    not figures."""
    t = text or ""
    for tok in allowlist or ():
        if tok:
            t = re.sub(re.escape(str(tok)), " ", t, flags=re.I)
    figs: List[Figure] = []
    for m in _NUM_RE.finditer(t):
        digits = m.group("int").replace(",", "") + (m.group("dec") or "")
        try:
            value = Decimal(digits)
        except InvalidOperation:
            continue
        suf = (m.group("suf") or "").strip().lower().replace(" ", "")
        is_usd = bool(m.group("usd") or m.group("usd2"))
        bare = not suf and not is_usd and "," not in m.group("int") and not m.group("dec")
        if bare and Decimal(1900) <= value <= Decimal(2099):
            continue                                                  # a year, not a statistic
        kind = "num"
        if suf == "%" or suf.startswith("per"):
            kind = "pct"
        elif suf in _MULT:
            value = value * (Decimal(10) ** _MULT[suf])
        if is_usd:
            kind = "usd"
        figs.append(Figure(raw=_clean(m.group(0)), kind=kind, value=value.normalize()))
    for m in _WORDFIG_RE.finditer(t):
        if m.group(1).lower() == "one" and not m.group(2):
            continue                                                  # "one day" is an idiom, not a stat
        figs.append(Figure(raw=_clean(m.group(0)), kind="phrase"))
    for m in _LOOSE_RE.finditer(t):
        figs.append(Figure(raw=_clean(m.group(0)), kind="phrase"))
    return figs


@dataclass
class TextIndex:
    figs: set
    norm: str


def index_text(text: str) -> TextIndex:
    figs = {f.key for f in extract_figures(text) if f.kind != "phrase"}
    # also index bare years and spelled-out small numbers as plain numbers
    for m in re.finditer(r"(?<![\w.,/])(\d{4})(?![\w,]|\.\d)", text or ""):
        figs.add(("num", Decimal(m.group(1)).normalize()))
    for w in re.findall(rf"\b{_W}\b", (text or "").lower()):
        figs.add(("num", Decimal(_WORDS[w]).normalize()))
    return TextIndex(figs=figs, norm=_norm(text).replace("-", " "))


def figure_in(fig: Figure, idx: TextIndex) -> bool:
    """Does this figure literally appear in the indexed text? Commas, %, the
    word 'percent', $ spacing and k/M/million suffixes are normalized away."""
    if fig.kind == "phrase":
        return _norm(fig.raw).replace("-", " ") in idx.norm
    if fig.key in idx.figs:
        return True
    # "54%" in the post vs a bare "54" in a source table. Only for values with
    # at least two significant digits, so "5%" never matches a stray "5".
    if fig.kind in ("pct", "usd") and ("num", fig.value) in idx.figs:
        return fig.value >= 10 or fig.value != fig.value.to_integral_value()
    return False


def _short(s: str, n: int = 170) -> str:
    s = _clean(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def check_figures(units: List[Unit], fetched: Dict[str, Fetched], facts_text: str,
                  post_sources: List[str], allowlist: Iterable[str] = ()) -> Tuple[List[str], Dict[str, int]]:
    """The code check for 'a real link that does not say what the post claims'.
    A figure passes only if it is in the brand facts file, or it sits in a
    sentence with a source link whose FETCHED TEXT contains the same figure."""
    violations: List[str] = []
    facts_idx = index_text(facts_text or "")
    idx_cache: Dict[str, TextIndex] = {}
    verified: set = set()
    checked = unmatched = 0

    def _idx(href: str) -> Optional[TextIndex]:
        f = fetched.get(href)
        if not f or f.state != "live" or not f.text:
            return None
        if href not in idx_cache:
            idx_cache[href] = index_text(f.text)
        return idx_cache[href]

    for u in units:
        if u.surface != "body":
            continue
        for fig in extract_figures(u.text, allowlist):
            checked += 1
            if facts_text and figure_in(fig, facts_idx):
                verified.add(fig.key)
                continue
            candidates = [h for h in u.links if h in fetched] or \
                         [h for h in post_sources if h in fetched]
            if not candidates:
                unmatched += 1
                violations.append(f'figure "{fig.raw}" has no source link in its sentence '
                                  f'and is not in the brand facts file: "{_short(u.text)}"')
                continue
            if any((i := _idx(h)) is not None and figure_in(fig, i) for h in candidates):
                verified.add(fig.key)
                continue
            unmatched += 1
            readable = [h for h in candidates if _idx(h) is not None]
            if readable:
                violations.append(f'figure "{fig.raw}" does not appear in the cited source '
                                  f'({", ".join(readable)}): "{_short(u.text)}"')
            else:
                violations.append(f'figure "{fig.raw}" cannot be confirmed because its source '
                                  f'did not load or has no readable text ({", ".join(candidates)}): '
                                  f'"{_short(u.text)}"')
    for u in units:
        if u.surface != "secondary":
            continue
        for fig in extract_figures(u.text, allowlist):
            checked += 1
            if fig.key in verified or (facts_text and figure_in(fig, facts_idx)):
                continue
            unmatched += 1
            violations.append(f'figure "{fig.raw}" in the {u.where} is not a sourced, verified '
                              f'figure from the article body: "{_short(u.text)}"')
    return violations, {"figures_checked": checked, "figures_unmatched": unmatched}


# ----------------------------------------------------------------------------
# Banned content
# ----------------------------------------------------------------------------

def check_banned(units: List[Unit], rules: List[Dict[str, Any]], facts_text: str = "") -> List[str]:
    """Per-brand banned content. A rule with `unless_in_facts: true` lets a
    sentence through only when that exact sentence is in the brand facts file
    (an approved founder experience, for example)."""
    violations: List[str] = []
    facts_norm = _norm(facts_text)
    compiled = []
    for rule in rules or []:
        try:
            pats = [re.compile(p, re.I) for p in (rule.get("patterns") or [])]
        except re.error as e:
            raise GateError(f"banned rule '{rule.get('id')}' has an invalid pattern: {e}")
        compiled.append((rule, pats))
    for u in units:
        for rule, pats in compiled:
            hit = next((m for p in pats if (m := p.search(u.text))), None)
            if not hit:
                continue
            if rule.get("unless_in_facts") and facts_norm and _norm(u.text) in facts_norm:
                continue
            violations.append(f"banned content [{rule.get('id', 'rule')}] "
                              f"{rule.get('label', '')} (matched \"{hit.group(0)}\" in {u.where}): "
                              f'"{_short(u.text)}"')
    return violations


# ----------------------------------------------------------------------------
# LLM verifier (claim level)
# ----------------------------------------------------------------------------

VERIFIER_SYSTEM = (
    "You are the fact-check verifier for a blog post that a DIFFERENT AI model wrote. "
    "You judge ONLY from the text supplied in this message. Never use outside knowledge, "
    "never assume a source says something it does not show, and never give the writer the "
    "benefit of the doubt.\n\n"
    "STEP 1. List every CHECKABLE claim in the post sentences. A checkable claim is a "
    "statement of fact about the world: a statistic, a date, an event, a legal or regulatory "
    "statement, what a named company, product or kind of software does, how often something "
    "happens ('most', 'usually', 'never'), a timeline or duration, and ANY claim about the "
    "brand itself (what it does, a capability, a price, a turnaround time, a guarantee, an "
    "experience the author says they had). Do NOT list opinions, advice, definitions, or "
    "plainly hypothetical illustrations.\n\n"
    "STEP 2. Judge each claim.\n"
    '- kind "external": a claim about the world. It is SUPPORTED only if a SOURCE available '
    "to that sentence contains a passage that states it. A source is available to a sentence "
    "when its URL is listed in that sentence's LINKS, or when it is listed under POST-LEVEL "
    "SOURCES.\n"
    '- kind "brand": a claim about the brand itself. It is SUPPORTED only if APPROVED BRAND '
    "FACTS states it, or a page of the brand's own site that is listed in that sentence's "
    "LINKS states it.\n"
    "Verdicts:\n"
    '- "SUPPORTED": you MUST give "source" (the exact source URL, or the word facts) and '
    '"quote": a passage copied CHARACTER FOR CHARACTER from that source, 8 to 45 words, that '
    "states the claim. Do not paraphrase, trim with an ellipsis, or stitch passages together. "
    "A program checks that your quote is really in the source.\n"
    '- "NOT_SUPPORTED": a source is available but does not state the claim, states something '
    "weaker, or contradicts it.\n"
    '- "UNVERIFIABLE": no source is available to the sentence, or the source text is missing.\n\n'
    "Return ONLY a JSON object, no prose and no code fence:\n"
    '{"claims": [{"sentence_id": "S3", "claim": "short restatement", "kind": "external", '
    '"source": "https://... or facts or null", "verdict": "SUPPORTED", "quote": "verbatim passage or empty"}]}'
)


def build_verifier_prompt(units: List[Unit], fetched: Dict[str, Fetched], facts_text: str,
                          post_sources: List[str]) -> str:
    lines = ["POST SENTENCES (id, where it sits, text, links in that sentence):"]
    for u in units:
        if u.surface == "meta" and u.where not in ("title",):
            continue
        link_s = f" LINKS: {', '.join(u.links)}" if u.links else ""
        lines.append(f"{u.uid} [{u.where}] {u.text}{link_s}")
    lines.append("")
    lines.append("POST-LEVEL SOURCES: " + (", ".join(post_sources) if post_sources else "(none)"))
    lines.append("")
    lines.append("APPROVED BRAND FACTS (the only allowed support for claims about the brand):")
    lines.append(facts_text.strip() if (facts_text or "").strip() else "(none on file)")
    lines.append("")
    lines.append("SOURCES (fetched text; the only evidence you may use for external claims):")
    budget = _PROMPT_TOTAL_SOURCE_CHARS
    any_source = False
    for href, f in fetched.items():
        if f.state != "live":
            lines.append(f"=== SOURCE {href} === (did not load: {f.detail})")
            continue
        take = min(len(f.text), _PROMPT_SOURCE_CHARS, max(budget, 0))
        budget -= take
        any_source = True
        body = f.text[:take] if take else "(text omitted: prompt budget exhausted)"
        if not f.text:
            body = f"(no readable text: {f.detail or 'empty page'})"
        lines.append(f"=== SOURCE {href} ===\n{body}")
    if not any_source:
        lines.append("(no source loaded)")
    return "\n".join(lines)


def evaluate_verifier(reply: Any, units: List[Unit], fetched: Dict[str, Fetched],
                      facts_text: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Turn the verifier's reply into violations + an audit record per claim.
    A SUPPORTED verdict is accepted only when CODE finds the quoted passage in
    the fetched source text (or the facts file). A quote the model made up is
    treated as NOT SUPPORTED."""
    if not isinstance(reply, dict) or not isinstance(reply.get("claims"), list):
        raise GateError("verifier reply has no 'claims' list")
    by_id = {u.uid: u for u in units}
    facts_norm = _norm(facts_text)
    norm_cache: Dict[str, str] = {}
    violations: List[str] = []
    records: List[Dict[str, Any]] = []
    for c in reply["claims"]:
        if not isinstance(c, dict):
            raise GateError("verifier reply contains a non-object claim")
        unit = by_id.get(str(c.get("sentence_id") or "").strip())
        sentence = unit.text if unit else str(c.get("claim") or "")
        claim = _clean(str(c.get("claim") or sentence))
        verdict = str(c.get("verdict") or "").strip().upper().replace(" ", "_")
        source = str(c.get("source") or "").strip()
        quote = str(c.get("quote") or "").strip()
        final = verdict
        if verdict == "SUPPORTED":
            qn = _norm(quote)
            if len(qn) < _MIN_QUOTE_CHARS:
                final = "QUOTE_MISSING"
            elif source.lower() == "facts":
                final = "SUPPORTED" if facts_norm and qn in facts_norm else "QUOTE_NOT_IN_SOURCE"
            else:
                f = fetched.get(source)
                if not f or f.state != "live" or not f.text:
                    final = "QUOTE_NOT_IN_SOURCE"
                else:
                    if source not in norm_cache:
                        norm_cache[source] = _norm(f.text)
                    final = "SUPPORTED" if qn in norm_cache[source] else "QUOTE_NOT_IN_SOURCE"
        elif verdict not in ("NOT_SUPPORTED", "UNVERIFIABLE"):
            final = "UNVERIFIABLE"
        records.append({"sentence_id": unit.uid if unit else "", "claim": claim,
                        "source": source or "", "verdict": final,
                        "quote": quote if final == "SUPPORTED" else ""})
        if final == "SUPPORTED":
            continue
        reason = {
            "NOT_SUPPORTED": "claim is not supported by its source",
            "UNVERIFIABLE": "unverifiable claim (no usable source or approved brand fact; source it or cut it)",
            "QUOTE_NOT_IN_SOURCE": "verifier marked this supported but its quoted passage is not in "
                                   "the source text (treated as not supported)",
            "QUOTE_MISSING": "verifier marked this supported without a usable quoted passage "
                             "(treated as not supported)",
        }[final]
        violations.append(f'{reason}: "{_short(sentence)}"'
                          + (f" [source: {source}]" if source else ""))
    return violations, records


# ----------------------------------------------------------------------------
# LLM editor (binary rubric)
# ----------------------------------------------------------------------------

# (id, the yes/no question, True when a PASS must quote the line that earns it)
EDITOR_RUBRIC: List[Tuple[str, str, bool]] = [
    ("answers_title", "The first paragraph directly answers the question the title asks.", True),
    ("concrete_specific", "The post contains at least one concrete specific a reader could not get "
                          "from a generic search result (a sourced number, a named system, a real "
                          "process detail from doing the work).", True),
    ("no_filler_or_hype", "There is no filler or hype: no padding, no sentence that only restates "
                          "its heading, no inflated or sales-pitch line.", False),
    ("no_unsupported_absolutes", "No factual sentence leans on an absolute or frequency word "
                                 "('most', 'never', 'always', 'none', 'every', 'usually') without a "
                                 "source link in that sentence.", False),
    ("no_invented_timelines", "No duration, turnaround time or schedule is stated without a source "
                              "link or an approved brand fact.", False),
    ("no_em_dashes", "There are no em-dashes anywhere.", False),
    ("operator_voice", "It reads like an operator who does this work wrote it: specific and plain, "
                       "no template phrasing, no outsider framing such as 'the vendor' or 'most "
                       "agencies'.", False),
]

EDITOR_SYSTEM = (
    "You are the editor for a blog post that a DIFFERENT AI model wrote. You are strict. A model "
    "grading generously is the failure this step exists to prevent, so when in doubt, fail the item.\n\n"
    "Answer every rubric item with pass true or false. Every answer needs \"evidence\": one line "
    "copied CHARACTER FOR CHARACTER from the post. For a pass, quote the line that earns the pass. "
    "For a fail, quote the line that causes the fail. If an item passes because something is absent "
    "(for example there are no em-dashes), evidence may be an empty string.\n\n"
    "Return ONLY a JSON object, no prose and no code fence:\n"
    '{"items": [{"id": "answers_title", "pass": true, "evidence": "verbatim line"}]}'
)


def build_editor_prompt(post: Dict[str, Any], units: List[Unit], facts_text: str) -> str:
    lines = [f"TITLE: {post.get('title', '')}", "", "RUBRIC (answer every id):"]
    for rid, question, _needs in EDITOR_RUBRIC:
        lines.append(f"- {rid}: {question}")
    lines += ["", "APPROVED BRAND FACTS:",
              facts_text.strip() if (facts_text or "").strip() else "(none on file)",
              "", "POST (one sentence per line; [link] marks a sentence that carries a source link):"]
    for u in units:
        if u.surface == "meta" and u.where != "title":
            continue
        lines.append(f"[{u.where}] {u.text}{' [link]' if u.links else ''}")
    return "\n".join(lines)


def evaluate_editor(reply: Any, units: List[Unit]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Rubric reply -> violations. Every rubric id must be answered. A pass on an
    evidence-required item only counts if the quoted line is really in the post."""
    if not isinstance(reply, dict) or not isinstance(reply.get("items"), list):
        raise GateError("editor reply has no 'items' list")
    got = {str((i or {}).get("id") or ""): i for i in reply["items"] if isinstance(i, dict)}
    post_norm = " \n ".join(_norm(u.text) for u in units)
    has_em_dash = any(_EM_DASH in u.text for u in units)
    violations: List[str] = []
    records: List[Dict[str, Any]] = []
    for rid, question, needs_evidence in EDITOR_RUBRIC:
        item = got.get(rid)
        if item is None or not isinstance(item.get("pass"), bool):
            raise GateError(f"editor reply is missing a pass/fail answer for '{rid}'")
        passed = item["pass"]
        evidence = _clean(str(item.get("evidence") or ""))
        if rid == "no_em_dashes" and has_em_dash:
            passed = False
        if passed and needs_evidence:
            ev = _norm(evidence)
            if len(ev) < 12 or ev not in post_norm:
                passed = False
                violations.append(f"editor: {rid} was passed with evidence that is not in the post "
                                  f'(treated as fail): "{_short(evidence)}"')
                records.append({"id": rid, "pass": False, "evidence": evidence})
                continue
        records.append({"id": rid, "pass": passed, "evidence": evidence})
        if not passed:
            violations.append(f'editor: {rid} FAIL ({question}) evidence: "{_short(evidence)}"')
    return violations, records


# ----------------------------------------------------------------------------
# Default LLM transport (a different model than the writer)
# ----------------------------------------------------------------------------

def default_llm(model: str) -> Callable[[str, str], Dict[str, Any]]:
    """LLM callable bound to the verifier model. Reuses the writer's Anthropic
    Messages transport (same ANTHROPIC_API_KEY, same spend ledger) with a
    different model id. Refuses to run on the writer's own model."""
    from services import slipstream_generate as gen
    if model.strip().lower() == str(gen._MODEL).strip().lower():
        raise GateError(f"verifier model '{model}' is the same as the writer model; a model "
                        "grading its own output over-passes it. Set SLIPSTREAM_VERIFIER_MODEL "
                        "to a different model.")

    def _call(system: str, user: str) -> Dict[str, Any]:
        return gen._llm_json_once(system, user, model=model, max_tokens=16000,
                                  timeout=300, surface="slipstream_factcheck")
    return _call


# ----------------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------------

def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def run_gate(post: Dict[str, Any], cfg: Dict[str, Any], *,
             http_get: Optional[Callable[[str], Any]] = None,
             llm: Optional[Callable[[str, str], Dict[str, Any]]] = None,
             facts_text: Optional[str] = None,
             model: Optional[str] = None,
             link_cache: Optional[Dict[str, Fetched]] = None,
             use_llm: bool = True,
             skip_llm_on_deterministic_fail: bool = False) -> Dict[str, Any]:
    """Run the whole gate on one generated post (either body shape).

    Returns {"ok", "violations", "error", "claims", "editor", "links", "counts",
    "model"}. NEVER raises: any failure inside the gate returns ok=False with an
    `error`, so the caller HOLDS the post. Kill-safe by construction."""
    model = model or verifier_model()
    out: Dict[str, Any] = {"ok": False, "violations": [], "error": None, "claims": [],
                           "editor": [], "links": [], "counts": {}, "model": model,
                           "llm_ran": False}
    try:
        fc = (cfg or {}).get("factcheck") or {}
        facts = load_facts(cfg) if facts_text is None else facts_text
        http_get = http_get or default_http_get
        units, post_sources = extract_post(post)

        link_v, fetched = check_links(units, (cfg or {}).get("domain", ""), http_get, link_cache)
        fig_v, fig_counts = check_figures(units, fetched, facts, post_sources,
                                          fc.get("figure_allowlist") or ())
        ban_v = check_banned(units, fc.get("banned") or [], facts)
        det = link_v + fig_v + ban_v

        out["links"] = [{"href": h, "state": f.state, "status": f.status, "internal": f.internal}
                        for h, f in fetched.items()]
        counts = {
            "links_checked": len(fetched),
            "links_dead": sum(1 for f in fetched.values() if f.state == "dead"),
            "links_unchecked": sum(1 for f in fetched.values() if f.state == "unchecked"),
            **fig_counts,
            "banned_hits": len(ban_v),
            "deterministic_violations": len(det),
        }

        ver_v: List[str] = []
        ed_v: List[str] = []
        if use_llm and not (skip_llm_on_deterministic_fail and det):
            llm = llm or default_llm(model)
            ver_reply = llm(VERIFIER_SYSTEM, build_verifier_prompt(units, fetched, facts, post_sources))
            ver_v, out["claims"] = evaluate_verifier(ver_reply, units, fetched, facts)
            ed_reply = llm(EDITOR_SYSTEM, build_editor_prompt(post, units, facts))
            ed_v, out["editor"] = evaluate_editor(ed_reply, units)
            out["llm_ran"] = True
        claims = out["claims"]
        counts.update({
            "claims": len(claims),
            "claims_supported": sum(1 for c in claims if c["verdict"] == "SUPPORTED"),
            "claims_not_supported": sum(1 for c in claims if c["verdict"] != "SUPPORTED"),
            "editor_fails": sum(1 for e in out["editor"] if not e["pass"]),
        })
        out["violations"] = _dedupe(det + ver_v + ed_v)
        counts["violations"] = len(out["violations"])
        out["counts"] = counts
        out["ok"] = not out["violations"]
        return out
    except Exception as e:  # kill-safe: a broken gate HOLDS, it never publishes
        logger.exception("[slipstream] factcheck gate errored; post will be HELD")
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        out["violations"] = [f"factcheck gate error (post HELD, never published unchecked): {out['error']}"]
        out["counts"] = {**out.get("counts", {}), "violations": 1}
        return out


def summarize(results: List[Dict[str, Any]], revised: bool) -> Dict[str, Any]:
    """The `factcheck` block of the run_brand receipt, built from the gate
    pass(es) that ran. Counts are those of the LAST pass."""
    last = results[-1] if results else {}
    return {
        "ran": bool(results),
        "passed": bool(last.get("ok")),
        "model": last.get("model"),
        "revised": bool(revised),
        "passes": len(results),
        "llm_ran": bool(last.get("llm_ran")),
        "error": last.get("error"),
        "counts": dict(last.get("counts") or {}),
        "first_pass_violations": len((results[0].get("violations") or [])) if results else 0,
        "claims": list(last.get("claims") or []),
    }


def _cell(s: Any) -> str:
    return _short(str(s or ""), 140).replace("|", "\\|") or "-"


def pr_table(summary: Optional[Dict[str, Any]]) -> str:
    """Markdown audit table for the PR body: each claim, its source, the verdict."""
    if not summary or not summary.get("ran"):
        return ""
    c = summary.get("counts") or {}
    head = (f"\n\n### Fact-check gate\n\n"
            f"- verifier model: `{summary.get('model')}` (not the writer model)\n"
            f"- revised once by the writer: {'yes' if summary.get('revised') else 'no'}\n"
            f"- links checked: {c.get('links_checked', 0)}, figures checked: "
            f"{c.get('figures_checked', 0)}, claims judged: {c.get('claims', 0)}\n\n")
    claims = summary.get("claims") or []
    if not claims:
        return head + "No checkable claims were found in the post.\n"
    rows = ["| Claim | Source | Verdict |", "|---|---|---|"]
    for cl in claims:
        rows.append(f"| {_cell(cl.get('claim'))} | {_cell(cl.get('source'))} | {_cell(cl.get('verdict'))} |")
    return head + "\n".join(rows) + "\n"
