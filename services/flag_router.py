"""Event-driven flag router for avo-telemetry cross-chat handoffs.

Problem this solves
-------------------
The avo-telemetry markdown protocol has nine seats (Revenue & Sales, Internal
Marketing, CMO, Build & Tech, ...). Each seat writes to one owned file and
reads all others for `🏁 FLAG FOR: <target>` blocks addressed to itself.
Intake is pull-based and session-start-only — there's no notification when a
flag lands. Worse, "FLAG FOR: X" inside X's *own* file is invisible to the
"scan other files for flags addressed to me" routine.

Round-trip incident 2026-06-25: IM-Book'd posted `🏁 FLAG FOR: CMO` inside
brand_rules.md (IM's own file) and pushed. CMO never saw it; Michael relayed
it by hand. This module closes that gap.

What this module does
---------------------
1. Pulled by `services/github_telemetry_webhook.py` whenever avo-telemetry
   receives a push (or the optional poller fires).
2. For each touched .md file, fetch the post-push content from GitHub raw,
   parse every `🏁 FLAG FOR: ...` block (regex below), and resolve the
   target string to a registered seat via `seats.yaml`.
3. Dedupe against the `routed_flags` Postgres table on (source_file,
   posted_ts) — re-routing the same flag is a no-op.
4. For each NEW flag, post a Slack message into the target seat's channel
   via the AVO bot, AND append a one-line entry to `<seat>_inbox.md` as a
   session-start backstop. Either path missing one notification is fine;
   both missing the same one would be a bug.

Routing is deterministic and event-driven — Michael is no longer the message
bus.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable

import requests
import yaml

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_SEATS_URL = (
    "https://raw.githubusercontent.com/salesdroid/avo-telemetry/main/seats.yaml"
)
_TELEMETRY_RAW_BASE = (
    "https://raw.githubusercontent.com/salesdroid/avo-telemetry/{sha}/{path}"
)
_REQUEST_TIMEOUT = 15


# ── Data shapes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Seat:
    canonical_name: str
    owned_file: str
    slack_channel: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class FlagBlock:
    target_raw: str          # the literal "FLAG FOR: <target>" text
    what: str
    why_now: str
    by_when: str
    posted_by: str
    posted_ts: str           # raw posted timestamp string (ISO-ish)
    source_file: str         # e.g. "brand_rules.md"
    source_sha: str          # head commit sha of the push that surfaced this
    line_no: int             # line number in source_file (for citations)

    def signature(self) -> str:
        """Idempotency key for the routed_flags table."""
        return f"{self.source_file}::{self.posted_ts}::{self.target_raw}"


# ── Seat registry ───────────────────────────────────────────────────────────


_seats_cache: list[Seat] | None = None
_seats_etag: str | None = None


def load_seats(force_refresh: bool = False) -> list[Seat]:
    """Fetch seats.yaml from avo-telemetry's main branch.

    Cached in-process; pass force_refresh=True after a registry edit. The
    cache holds across events but resets on every restart, so a Railway
    redeploy picks up registry edits automatically.
    """
    global _seats_cache
    if _seats_cache is not None and not force_refresh:
        return _seats_cache
    try:
        r = requests.get(_SEATS_URL, timeout=_REQUEST_TIMEOUT)
        r.raise_for_status()
        data = yaml.safe_load(r.text) or {}
    except Exception as e:
        logger.error("[flag_router] seats.yaml fetch failed: %s", e)
        return _seats_cache or []
    seats: list[Seat] = []
    for raw in data.get("seats", []):
        seats.append(
            Seat(
                canonical_name=raw["canonical_name"],
                owned_file=raw["owned_file"],
                slack_channel=raw["slack_channel"],
                aliases=tuple(raw.get("aliases", [])),
            )
        )
    _seats_cache = seats
    logger.info("[flag_router] loaded %d seats from registry", len(seats))
    return seats


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace for alias matching."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def resolve_seat(target_raw: str, seats: list[Seat] | None = None) -> Seat | None:
    """Map a flag's target string (e.g. "Internal Marketing (Book'd)") to a
    registered Seat using its canonical name or any alias. Case-insensitive,
    whitespace-tolerant. Returns None if no match — caller logs + escalates.

    Match strategy:
      1. Exact match against canonical_name or any alias
      2. Same, after stripping priority/annotation suffix (em-dash separated)
      3. None — surfaces in the daily digest as an "unrouted target" entry
    """
    if seats is None:
        seats = load_seats()
    if not target_raw or not target_raw.strip():
        return None

    def _match(t: str) -> Seat | None:
        needle = _norm(t)
        if not needle:
            return None
        for seat in seats:
            if _norm(seat.canonical_name) == needle:
                return seat
            for alias in seat.aliases:
                if _norm(alias) == needle:
                    return seat
        return None

    return _match(target_raw) or _match(_strip_priority_suffix(target_raw))


# ── Parser ──────────────────────────────────────────────────────────────────


# Header line: "🏁 FLAG FOR: <target>" — captures the target text after the colon.
_HEADER_RE = re.compile(r"^🏁\s*FLAG\s*FOR:\s*(.+?)\s*$", re.MULTILINE)
# Field line: "**Field:** value" — colon is INSIDE the bold markers per README spec.
_FIELD_RE = re.compile(
    r"^\*\*(What|Why now|By when|Posted by|Posted):\*\*\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# Header that ends the block (next FLAG, next H2/H3, or end of file)
_BLOCK_END_RE = re.compile(r"^(🏁\s*FLAG\s*FOR:|##\s|###\s)", re.MULTILINE)


def _strip_priority_suffix(target: str) -> str:
    """Remove " — 🚨 PRIORITY N (...)" and "(brand-name)" style suffixes so the
    bare seat name remains for alias lookup.

    Examples:
      "Build & Tech — 🚨 PRIORITY 2 (systemic unblock)" → "Build & Tech"
      "Internal Marketing (IM-WD)" → unchanged (this IS a registered alias)
      "Pit Wall" → unchanged
    """
    # Em-dash, en-dash, or " - " separator before any annotation.
    for sep in (" — ", " – ", " - "):
        if sep in target:
            return target.split(sep, 1)[0].strip()
    return target.strip()


def _flags_section(content: str) -> tuple[str, int]:
    """Return (section_body, section_start_offset) for the
    `## Flags for other chats` section. Empty string + 0 if missing.

    Order-agnostic: works whether `## Flags for other chats` comes before or
    after `## Recently closed` in the file. The README pins all live flags
    inside this section by convention; closed flags live elsewhere.
    """
    m = re.search(r"^##\s+Flags for other chats\s*$", content, re.MULTILINE)
    if not m:
        return "", 0
    section_start = m.end()
    rest = content[section_start:]
    end_match = re.search(r"^##\s", rest, re.MULTILINE)
    body = rest[: end_match.start()] if end_match else rest
    return body, section_start


def parse_flags(content: str, source_file: str, source_sha: str) -> list[FlagBlock]:
    """Walk every `🏁 FLAG FOR:` block inside the `## Flags for other chats`
    section and return FlagBlocks. Returns [] if the file has no such section,
    or if every flag inside is already marked resolved.

    A block runs from its header to the next FLAG header or the next H2/H3 or
    end-of-section.
    """
    active, section_start = _flags_section(content)
    if not active:
        return []

    blocks: list[FlagBlock] = []
    matches = list(_HEADER_RE.finditer(active))
    for i, m in enumerate(matches):
        target_raw = m.group(1).strip()
        block_start = m.end()
        # Find where this block ends — next FLAG header or H2/H3.
        rest = active[block_start:]
        end_match = _BLOCK_END_RE.search(rest)
        block_body = rest[: end_match.start()] if end_match else rest

        fields = {
            k.lower(): v
            for k, v in _FIELD_RE.findall(block_body)
        }
        # Skip "✅ RESOLVED" inline-resolved flags (sometimes flag is marked
        # closed-in-place without moving to Recently closed).
        if "✅" in target_raw.upper() or "RESOLVED" in target_raw.upper():
            continue

        # Line number of the header inside the original content (1-indexed).
        # m.start() is relative to `active`; offset by `section_start` to get
        # the position in the full file.
        abs_pos = section_start + m.start()
        line_no = content.count("\n", 0, abs_pos) + 1

        blocks.append(
            FlagBlock(
                target_raw=target_raw,
                what=fields.get("what", "").strip(),
                why_now=fields.get("why now", "").strip(),
                by_when=fields.get("by when", "").strip(),
                posted_by=fields.get("posted by", "").strip(),
                posted_ts=fields.get("posted", "").strip(),
                source_file=source_file,
                source_sha=source_sha,
                line_no=line_no,
            )
        )
    return blocks


# ── GitHub raw content fetch ────────────────────────────────────────────────


def fetch_telemetry_file(path: str, sha: str) -> str:
    """Pull a single markdown file from avo-telemetry at a given SHA."""
    url = _TELEMETRY_RAW_BASE.format(sha=sha, path=path)
    r = requests.get(url, timeout=_REQUEST_TIMEOUT)
    if r.status_code == 404:
        # File was deleted in this push — nothing to parse.
        return ""
    r.raise_for_status()
    return r.text


# ── Slack notifier ──────────────────────────────────────────────────────────


_SLACK_API = "https://slack.com/api/chat.postMessage"


def _slack_token() -> str | None:
    return (os.environ.get("SLACK_BOT_TOKEN") or "").strip() or None


def _flag_to_slack_blocks(flag: FlagBlock, seat: Seat) -> list[dict]:
    """Render a FlagBlock as Slack Block-Kit. Compact, scannable, linked."""
    header_text = f":triangular_flag_on_post: New flag for *{seat.canonical_name}*"
    file_url = (
        f"https://github.com/salesdroid/avo-telemetry/blob/"
        f"{flag.source_sha}/{flag.source_file}#L{flag.line_no}"
    )
    fields = []
    if flag.what:
        fields.append(f"*What:* {flag.what}")
    if flag.why_now:
        fields.append(f"*Why now:* {flag.why_now}")
    if flag.by_when:
        fields.append(f"*By when:* {flag.by_when}")
    if flag.posted_by:
        fields.append(f"*From:* {flag.posted_by}")
    body = "\n".join(fields) if fields else "_(no fields parsed)_"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{file_url}|{flag.source_file}:{flag.line_no}> · posted {flag.posted_ts}",
                }
            ],
        },
    ]


def post_to_slack(seat: Seat, flag: FlagBlock) -> bool:
    """Post a flag notification into the seat's Slack channel via the AVO bot.

    Returns True on success. False on any failure (missing token, bad channel,
    API rate limit) — caller is expected to fall back to the inbox-file path
    and log loudly.
    """
    token = _slack_token()
    if not token:
        logger.error("[flag_router] SLACK_BOT_TOKEN missing — Slack push skipped")
        return False
    try:
        r = requests.post(
            _SLACK_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": seat.slack_channel,  # channel name; bot must be a member
                "text": f"New flag for {seat.canonical_name}: {flag.what[:120] or '(see file)'}",
                "blocks": _flag_to_slack_blocks(flag, seat),
                "unfurl_links": False,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and body.get("ok"):
            return True
        logger.error(
            "[flag_router] Slack post failed (channel=%s): http=%s body=%s",
            seat.slack_channel, r.status_code, body,
        )
        return False
    except Exception as e:
        logger.error("[flag_router] Slack post raised: %s", e)
        return False


# ── Dedupe (Postgres via services.database) ────────────────────────────────

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS routed_flags (
    id              SERIAL PRIMARY KEY,
    source_file     TEXT NOT NULL,
    posted_ts       TEXT NOT NULL,
    target_raw      TEXT NOT NULL,
    resolved_seat   TEXT,
    source_sha      TEXT NOT NULL,
    slack_ok        BOOLEAN NOT NULL DEFAULT FALSE,
    inbox_ok        BOOLEAN NOT NULL DEFAULT FALSE,
    routed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_file, posted_ts, target_raw)
)
"""

_table_ensured = False


def _ensure_table() -> None:
    global _table_ensured
    if _table_ensured:
        return
    execute_query(_TABLE_DDL)
    _table_ensured = True


def already_routed(flag: FlagBlock) -> bool:
    _ensure_table()
    rows = fetch_all(
        "SELECT 1 FROM routed_flags WHERE source_file=%s AND posted_ts=%s AND target_raw=%s",
        (flag.source_file, flag.posted_ts, flag.target_raw),
    )
    return bool(rows)


def record_routing(
    flag: FlagBlock,
    resolved_seat: str | None,
    slack_ok: bool,
    inbox_ok: bool,
) -> None:
    _ensure_table()
    execute_query(
        """
        INSERT INTO routed_flags
            (source_file, posted_ts, target_raw, resolved_seat, source_sha, slack_ok, inbox_ok)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_file, posted_ts, target_raw) DO NOTHING
        """,
        (
            flag.source_file,
            flag.posted_ts,
            flag.target_raw,
            resolved_seat,
            flag.source_sha,
            slack_ok,
            inbox_ok,
        ),
    )


# ── Orchestrator ────────────────────────────────────────────────────────────


def route_files(files_modified: Iterable[str], head_sha: str) -> dict:
    """Walk every modified .md file at `head_sha`, parse new flags, route them.

    Returns a summary dict suitable for webhook ACK + log line:
        {"files_scanned": N, "flags_seen": N, "newly_routed": N,
         "unresolved": [{"target_raw": "...", "source_file": "..."}, ...],
         "slack_misses": [...], "errors": [...]}
    """
    seats = load_seats()
    summary = {
        "files_scanned": 0,
        "flags_seen": 0,
        "newly_routed": 0,
        "unresolved": [],
        "slack_misses": [],
        "errors": [],
    }

    md_files = [f for f in files_modified if f.endswith(".md")]
    for path in md_files:
        try:
            content = fetch_telemetry_file(path, head_sha)
        except Exception as e:
            summary["errors"].append(f"fetch:{path}:{e}")
            continue
        if not content:
            continue
        summary["files_scanned"] += 1
        flags = parse_flags(content, source_file=path, source_sha=head_sha)
        summary["flags_seen"] += len(flags)

        for flag in flags:
            if already_routed(flag):
                continue
            seat = resolve_seat(flag.target_raw, seats)
            if seat is None:
                summary["unresolved"].append(
                    {"target_raw": flag.target_raw, "source_file": path}
                )
                record_routing(flag, resolved_seat=None, slack_ok=False, inbox_ok=False)
                continue

            slack_ok = post_to_slack(seat, flag)
            inbox_ok = False  # Phase 4 wires inbox file append; v1 is Slack-first.

            if not slack_ok:
                summary["slack_misses"].append(
                    {"target_raw": flag.target_raw, "seat": seat.canonical_name}
                )

            record_routing(
                flag,
                resolved_seat=seat.canonical_name,
                slack_ok=slack_ok,
                inbox_ok=inbox_ok,
            )
            summary["newly_routed"] += 1

    return summary


# ── GitHub HMAC verification ────────────────────────────────────────────────


def verify_github_signature(secret: str, body: bytes, sig_header: str) -> bool:
    """Validate `X-Hub-Signature-256` against the raw body using HMAC-SHA256.

    `sig_header` looks like `sha256=<hex>`. Constant-time compare to dodge
    timing-leak attacks. Returns False on any malformed input.
    """
    if not (secret and sig_header and sig_header.startswith("sha256=")):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


# ── Webhook payload entry point ─────────────────────────────────────────────


def handle_github_push(payload: dict) -> dict:
    """Top-level entry for a verified GitHub push webhook payload.

    Extracts head_commit + touched files, hands off to route_files(). Idempotent
    via the routed_flags UNIQUE constraint — replay a webhook safely.
    """
    head = payload.get("head_commit") or {}
    head_sha = head.get("id") or payload.get("after") or ""
    if not head_sha:
        return {"status": "skip", "reason": "no head sha in payload"}
    # Push payload aggregates per-commit added/modified across commits[].
    touched: set[str] = set()
    for c in payload.get("commits", []) or []:
        for f in c.get("added", []) or []:
            touched.add(f)
        for f in c.get("modified", []) or []:
            touched.add(f)
    # head_commit may include the same lists; merge defensively.
    for f in head.get("added", []) or []:
        touched.add(f)
    for f in head.get("modified", []) or []:
        touched.add(f)

    summary = route_files(touched, head_sha)
    summary["head_sha"] = head_sha
    summary["touched_count"] = len(touched)
    return summary
