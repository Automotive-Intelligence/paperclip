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
from dataclasses import dataclass, field
from typing import Iterable

import requests
import yaml

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_TELEMETRY_REPO = "salesdroid/avo-telemetry"
_GH_CONTENTS_URL = "https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
_REQUEST_TIMEOUT = 15


def _gh_headers() -> dict[str, str]:
    """GitHub API headers using GITHUB_TOKEN_TELEMETRY (avo-telemetry is private)."""
    token = (os.environ.get("GITHUB_TOKEN_TELEMETRY") or "").strip()
    h = {
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "paperclip-flag-router",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _fetch_telemetry_path(path: str, ref: str = "main") -> str:
    """Read a file from the (private) avo-telemetry repo via GitHub API.

    Returns "" on 404. Raises on transport errors so the caller can decide
    whether to ignore (digest) or surface (webhook).
    """
    url = _GH_CONTENTS_URL.format(repo=_TELEMETRY_REPO, path=path, ref=ref)
    r = requests.get(url, headers=_gh_headers(), timeout=_REQUEST_TIMEOUT)
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return r.text


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

    @property
    def body_hash(self) -> str:
        """16-char SHA-256 prefix of the fields that escalations mutate.

        A flag's *identity* is (source_file, posted_ts, target_raw) — the
        sender, when, and who it's for. A flag's *content* is what+why_now+
        by_when+posted_by. Pit Wall escalating a flag in place (ESCALATE-AGAIN,
        STALE-ALERT, scope rewrites) changes content while identity stays
        constant. Routing on identity alone treats the escalation as already-
        routed (silent). Routing on identity + body_hash makes the same flag
        with different content re-fire as an ESCALATED notification.
        """
        h = hashlib.sha256()
        for field_val in (self.what, self.why_now, self.by_when, self.posted_by):
            h.update((field_val or "").encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:16]

    def signature(self) -> str:
        """Identity key (without body_hash)."""
        return f"{self.source_file}::{self.posted_ts}::{self.target_raw}"


# ── Seat registry ───────────────────────────────────────────────────────────


_seats_cache: list[Seat] | None = None
_seats_etag: str | None = None


def load_seats(force_refresh: bool = False) -> list[Seat]:
    """Fetch seats.yaml from avo-telemetry's main branch (private repo, uses
    GITHUB_TOKEN_TELEMETRY).

    Cached in-process; pass force_refresh=True after a registry edit. The
    cache holds across events but resets on every restart, so a Railway
    redeploy picks up registry edits automatically.
    """
    global _seats_cache
    if _seats_cache is not None and not force_refresh:
        return _seats_cache
    try:
        text = _fetch_telemetry_path("seats.yaml")
        data = yaml.safe_load(text) or {}
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

    return (
        _match(target_raw)
        or _match(_strip_priority_suffix(target_raw))
        or _match(_strip_parenthetical(target_raw))
        or _match(_strip_parenthetical(_strip_priority_suffix(target_raw)))
    )


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
    """Remove " — 🚨 PRIORITY N (...)" suffix so the bare seat name remains.

    Examples:
      "Build & Tech — 🚨 PRIORITY 2 (systemic unblock)" → "Build & Tech"
      "Internal Marketing (IM-WD)" → unchanged (this IS a registered alias)
    """
    for sep in (" — ", " – ", " - "):
        if sep in target:
            return target.split(sep, 1)[0].strip()
    return target.strip()


def _strip_parenthetical(target: str) -> str:
    """Remove a trailing " (sub-scope)" so the bare seat name remains.

    Used as a fallback when the parenthetical is NOT a registered alias.
    "Internal Marketing (IM-WD)" matches an alias directly — never strips.
    "Build & Tech (Attio cleanup)" has no such alias — falls back to
    "Build & Tech".
    """
    s = target.strip()
    if s.endswith(")") and "(" in s:
        return s[: s.rindex("(")].strip()
    return s


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
    """Pull a single markdown file from avo-telemetry at a given SHA.

    Returns "" if the file was deleted in this push (404).
    """
    return _fetch_telemetry_path(path, ref=sha)


# ── Slack notifier ──────────────────────────────────────────────────────────


_SLACK_API = "https://slack.com/api/chat.postMessage"


def _slack_token() -> str | None:
    return (os.environ.get("SLACK_BOT_TOKEN") or "").strip() or None


def _flag_to_slack_blocks(flag: FlagBlock, seat: Seat, is_escalation: bool = False) -> list[dict]:
    """Render a FlagBlock as Slack Block-Kit. Compact, scannable, linked."""
    if is_escalation:
        header_text = f":rotating_light: *ESCALATED:* updated flag for *{seat.canonical_name}*"
    else:
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


def post_to_slack(
    seat: Seat,
    flag: FlagBlock,
    is_escalation: bool = False,
) -> tuple[bool, str | None, str | None]:
    """Post a flag notification into the seat's Slack channel via the AVO bot.

    Returns (ok, channel_id, parent_ts). channel_id + parent_ts are used by
    the flag_responder to post a threaded draft reply. Either is None on
    failure — caller logs + skips the responder enqueue.

    When is_escalation=True, header renders with `:rotating_light: ESCALATED:`
    so a re-fired flag is distinguishable from a brand-new one at a glance.
    """
    token = _slack_token()
    if not token:
        logger.error("[flag_router] SLACK_BOT_TOKEN missing — Slack push skipped")
        return False, None, None
    try:
        prefix = "ESCALATED: " if is_escalation else "New flag for "
        r = requests.post(
            _SLACK_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": seat.slack_channel,  # channel name; bot must be a member
                "text": f"{prefix}{seat.canonical_name}: {flag.what[:120] or '(see file)'}",
                "blocks": _flag_to_slack_blocks(flag, seat, is_escalation=is_escalation),
                "unfurl_links": False,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and body.get("ok"):
            return True, body.get("channel"), body.get("ts")
        logger.error(
            "[flag_router] Slack post failed (channel=%s): http=%s body=%s",
            seat.slack_channel, r.status_code, body,
        )
        return False, None, None
    except Exception as e:
        logger.error("[flag_router] Slack post raised: %s", e)
        return False, None, None


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

# Idempotent migration to add body_hash + is_escalation. Runs once per
# process at first call. The CREATE UNIQUE INDEX uses COALESCE so legacy
# rows with NULL body_hash don't trigger constraint violation on insert
# of a fresh-hash row for the same flag identity.
_TABLE_MIGRATIONS = [
    "ALTER TABLE routed_flags ADD COLUMN IF NOT EXISTS body_hash TEXT",
    "ALTER TABLE routed_flags ADD COLUMN IF NOT EXISTS is_escalation BOOLEAN NOT NULL DEFAULT FALSE",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS routed_flags_unique_v2_idx "
        "ON routed_flags (source_file, posted_ts, target_raw, COALESCE(body_hash, ''))"
    ),
]

_table_ensured = False


def _ensure_table() -> None:
    global _table_ensured
    if _table_ensured:
        return
    execute_query(_TABLE_DDL)
    for ddl in _TABLE_MIGRATIONS:
        try:
            execute_query(ddl)
        except Exception as e:
            logger.warning("[flag_router] migration step skipped: %s (%s)", ddl[:60], e)
    _table_ensured = True


def check_routed_or_escalation(flag: FlagBlock) -> tuple[bool, bool]:
    """Decide whether to route this flag and whether it's an escalation.

    Returns (skip, is_escalation):
      - (True,  False) — exact same flag (identity + body_hash) already routed
      - (True,  False) — legacy row exists with NULL body_hash; backfilled in place
      - (False, True ) — same identity exists with different body_hash → escalation
      - (False, False) — never seen → first routing
    """
    _ensure_table()
    bh = flag.body_hash

    # Case A: exact match (identity + body) → already routed
    rows = fetch_all(
        """
        SELECT 1 FROM routed_flags
        WHERE source_file=%s AND posted_ts=%s AND target_raw=%s AND body_hash=%s
        LIMIT 1
        """,
        (flag.source_file, flag.posted_ts, flag.target_raw, bh),
    )
    if rows:
        return True, False

    # Case B: legacy row (NULL body_hash) for this identity → backfill, don't re-fire
    legacy = fetch_all(
        """
        SELECT id FROM routed_flags
        WHERE source_file=%s AND posted_ts=%s AND target_raw=%s AND body_hash IS NULL
        LIMIT 1
        """,
        (flag.source_file, flag.posted_ts, flag.target_raw),
    )
    if legacy:
        execute_query(
            "UPDATE routed_flags SET body_hash=%s WHERE id=%s",
            (bh, legacy[0][0]),
        )
        return True, False

    # Case C: identity exists with a DIFFERENT body_hash → escalation
    prior = fetch_all(
        """
        SELECT 1 FROM routed_flags
        WHERE source_file=%s AND posted_ts=%s AND target_raw=%s AND body_hash IS NOT NULL
        LIMIT 1
        """,
        (flag.source_file, flag.posted_ts, flag.target_raw),
    )
    if prior:
        return False, True

    # Case D: truly new
    return False, False


# Back-compat shim: older callers used a single bool. New code uses
# check_routed_or_escalation. Keep this around so any out-of-tree caller
# doesn't break, but route_files now uses the richer check above.
def already_routed(flag: FlagBlock) -> bool:
    skip, _ = check_routed_or_escalation(flag)
    return skip


def record_routing(
    flag: FlagBlock,
    resolved_seat: str | None,
    slack_ok: bool,
    inbox_ok: bool,
    is_escalation: bool = False,
) -> None:
    _ensure_table()
    execute_query(
        """
        INSERT INTO routed_flags
            (source_file, posted_ts, target_raw, resolved_seat, source_sha,
             slack_ok, inbox_ok, body_hash, is_escalation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            flag.source_file,
            flag.posted_ts,
            flag.target_raw,
            resolved_seat,
            flag.source_sha,
            slack_ok,
            inbox_ok,
            flag.body_hash,
            is_escalation,
        ),
    )


# ── Orchestrator ────────────────────────────────────────────────────────────


def _spawn_responder(
    seat: Seat,
    flag: FlagBlock,
    channel_id: str,
    thread_ts: str,
    is_escalation: bool = False,
) -> None:
    """Run the flag_responder in a daemon thread. Never raises into the caller."""
    import threading

    def _run():
        try:
            from services.flag_responder import respond_to_flag
            resp = respond_to_flag(
                seat, flag,
                slack_channel_id=channel_id,
                slack_thread_ts=thread_ts,
                is_escalation=is_escalation,
            )
            logger.info(
                "[flag_router] responder finished: %s/%s status=%s verdict=%s",
                seat.canonical_name, flag.source_file,
                resp.get("status"), resp.get("judge_verdict"),
            )
        except Exception as e:
            logger.error(
                "[flag_router] responder thread failed for %s: %s",
                seat.canonical_name, e, exc_info=True,
            )

    t = threading.Thread(target=_run, daemon=True, name=f"flag-responder-{seat.canonical_name}")
    t.start()


def route_files(
    files_modified: Iterable[str],
    head_sha: str,
    seed_only: bool = False,
) -> dict:
    """Walk every modified .md file at `head_sha`, parse new flags, route them.

    Returns a summary dict suitable for webhook ACK + log line:
        {"files_scanned": N, "flags_seen": N, "newly_routed": N,
         "newly_escalated": N, "seeded": N,
         "unresolved": [{"target_raw": "...", "source_file": "..."}, ...],
         "slack_misses": [...], "errors": [...]}

    `seed_only=True`: record routing rows in routed_flags but DON'T post to
    Slack. Used once at first deploy to backfill historical flags into the
    dedupe table — so live routes only fire on genuinely new flags after
    cutover, not 27 historical ones in one burst.
    """
    seats = load_seats()
    summary = {
        "files_scanned": 0,
        "flags_seen": 0,
        "newly_routed": 0,
        "newly_escalated": 0,
        "seeded": 0,
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
            skip, is_escalation = check_routed_or_escalation(flag)
            if skip:
                continue
            seat = resolve_seat(flag.target_raw, seats)
            if seat is None:
                summary["unresolved"].append(
                    {"target_raw": flag.target_raw, "source_file": path}
                )
                record_routing(
                    flag, resolved_seat=None,
                    slack_ok=False, inbox_ok=False,
                    is_escalation=is_escalation,
                )
                continue

            if seed_only:
                # Backfill mode — record but don't post.
                record_routing(
                    flag,
                    resolved_seat=seat.canonical_name,
                    slack_ok=False,
                    inbox_ok=False,
                    is_escalation=is_escalation,
                )
                summary["seeded"] += 1
                continue

            slack_ok, slack_channel_id, slack_parent_ts = post_to_slack(
                seat, flag, is_escalation=is_escalation,
            )
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
                is_escalation=is_escalation,
            )
            if is_escalation:
                summary["newly_escalated"] += 1
            else:
                summary["newly_routed"] += 1

            # Fire the flag responder in a background thread so the GitHub
            # webhook returns quickly (10s timeout) while the LLM call
            # (5-15s) runs out of band. Responder posts the threaded reply
            # whenever it completes — usually within ~10-20s.
            if slack_ok and slack_channel_id and slack_parent_ts:
                _spawn_responder(
                    seat, flag, slack_channel_id, slack_parent_ts,
                    is_escalation=is_escalation,
                )
                summary["responders_started"] = summary.get("responders_started", 0) + 1

    return summary


# ── Daily unresolved-flag digest ────────────────────────────────────────────


_DIGEST_TRACKED_FILES = [
    "revenue_state.md", "client_situations.md", "content_pipeline.md",
    "brand_rules.md", "sales_pipeline.md", "client_campaigns.md",
    "cmo_state.md", "infrastructure_state.md", "strategic_calls.md",
]
_DIGEST_AGE_THRESHOLD_HOURS = 24


def _parse_iso(ts: str):
    """Parse the variety of timestamp formats flags use (ISO with/without TZ).
    Returns None if unparseable."""
    import datetime as _dt
    if not ts:
        return None
    s = ts.strip().rstrip("Z").replace("Z", "")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def compute_digest(now=None) -> dict:
    """Scan the 9 owned files for open flags older than 24h, grouped by seat.

    Returns:
        {
          "as_of": "<ISO>",
          "by_seat": {
            "Build & Tech": [FlagBlock, ...],
            ...
          },
          "unresolved_targets": [{"target_raw", "source_file", "age_hours"}],
          "total_open": N,
          "total_aged": N,
        }
    """
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    seats = load_seats()
    by_seat: dict[str, list[FlagBlock]] = {}
    unresolved_targets: list[dict] = []
    total_open = 0
    total_aged = 0

    for path in _DIGEST_TRACKED_FILES:
        try:
            content = fetch_telemetry_file(path, "main")
        except Exception as e:
            logger.warning("[digest] fetch failed for %s: %s", path, e)
            continue
        if not content:
            continue
        for flag in parse_flags(content, source_file=path, source_sha="main"):
            total_open += 1
            posted = _parse_iso(flag.posted_ts)
            if posted:
                age = now - posted.astimezone(_dt.timezone.utc) if posted.tzinfo else now.replace(tzinfo=None) - posted
                age_hours = age.total_seconds() / 3600.0
            else:
                age_hours = None
            if age_hours is None or age_hours < _DIGEST_AGE_THRESHOLD_HOURS:
                continue
            total_aged += 1
            seat = resolve_seat(flag.target_raw, seats)
            if seat is None:
                unresolved_targets.append({
                    "target_raw": flag.target_raw,
                    "source_file": flag.source_file,
                    "age_hours": round(age_hours, 1),
                })
                continue
            by_seat.setdefault(seat.canonical_name, []).append(flag)

    return {
        "as_of": now.isoformat(),
        "by_seat": by_seat,
        "unresolved_targets": unresolved_targets,
        "total_open": total_open,
        "total_aged": total_aged,
    }


def post_digest_to_slack(digest: dict) -> bool:
    """Render compute_digest() result as a Slack Block-Kit message in #pit-wall.

    Compact — listing every open >24h flag grouped by seat, plus any unrouted
    targets that need a new seats.yaml alias. Skip the post if there's nothing
    to surface (no flags older than threshold + no unrouted).
    """
    by_seat: dict = digest.get("by_seat", {})
    unresolved: list = digest.get("unresolved_targets", [])
    if not by_seat and not unresolved:
        logger.info("[digest] nothing to surface — skipping")
        return True

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Open flags > 24h ({digest.get('total_aged', 0)})"},
        },
    ]
    for seat_name, flags in sorted(by_seat.items()):
        lines = [f"*{seat_name}* ({len(flags)})"]
        for f in flags[:8]:  # cap per seat to keep digest readable
            file_url = (
                f"https://github.com/salesdroid/avo-telemetry/blob/main/"
                f"{f.source_file}#L{f.line_no}"
            )
            head = f.what[:80] + ("…" if len(f.what) > 80 else "") if f.what else "(see file)"
            lines.append(f"• <{file_url}|{f.source_file}:{f.line_no}> — {head}")
        if len(flags) > 8:
            lines.append(f"… + {len(flags)-8} more")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    if unresolved:
        lines = ["*Unrouted targets* (need seats.yaml alias)"]
        for u in unresolved[:10]:
            lines.append(f"• `{u['target_raw']}` in {u['source_file']} (age {u['age_hours']}h)")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    blocks.append(
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Digest computed {digest['as_of']}"},
        ]}
    )

    token = _slack_token()
    if not token:
        logger.error("[digest] SLACK_BOT_TOKEN missing — digest skipped")
        return False
    try:
        r = requests.post(
            _SLACK_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": "pit-wall",
                "text": f"Open flags > 24h: {digest.get('total_aged', 0)}",
                "blocks": blocks,
                "unfurl_links": False,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = bool(r.ok and body.get("ok"))
        if not ok:
            logger.error("[digest] Slack post failed: http=%s body=%s", r.status_code, body)
        return ok
    except Exception as e:
        logger.error("[digest] Slack post raised: %s", e)
        return False


def run_daily_digest():
    """APScheduler entry point — compute + post the digest. Catches all errors
    so a digest crash doesn't kill the scheduler."""
    try:
        digest = compute_digest()
        post_digest_to_slack(digest)
        logger.info(
            "[digest] daily run: open=%d aged=%d seats=%d unrouted=%d",
            digest.get("total_open", 0),
            digest.get("total_aged", 0),
            len(digest.get("by_seat", {})),
            len(digest.get("unresolved_targets", [])),
        )
    except Exception as e:
        logger.error("[digest] run failed: %s", e, exc_info=True)


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
