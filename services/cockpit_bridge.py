"""
services/cockpit_bridge.py

Bridges the AVO Cockpit's avo-telemetry repo (GitHub-backed markdown flags)
into Paperclip's agent_handoffs PostgreSQL table.

Flow:
  1. Poll avo-telemetry every 60s via GitHub API.
  2. Parse the "Flags for other chats" section of each of the 8 telemetry files.
  3. For each flag not yet seen, map the target chat to a Paperclip agent/river
     and call core.handoff.create_handoff().
  4. Record the mapping in cockpit_bridge_seen_flags so we do not double-create.
  5. On a separate poll, find bridge-created handoffs that have been marked
     complete, and commit a flag removal to avo-telemetry via GitHub API.

Design constraints:
  - No new infra. Uses existing Paperclip Postgres and APScheduler.
  - Polling (not webhook) for v1. Webhook is a v2 optimization.
  - One-way content flow: cockpit -> Paperclip for create, Paperclip ->
    cockpit for close. No in-flight edits sync back.
  - Build & Tech flags are skipped (Claude Code lane, not agent work).
"""

import base64
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.handoff import create_handoff
from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)


TELEMETRY_FILES: List[Dict[str, str]] = [
    {"filename": "revenue_state.md", "owner": "Revenue & Sales"},
    {"filename": "client_situations.md", "owner": "B2B Operations"},
    {"filename": "content_pipeline.md", "owner": "Agent Empire"},
    {"filename": "brand_rules.md", "owner": "Internal Marketing"},
    {"filename": "sales_pipeline.md", "owner": "Sales Marketing"},
    {"filename": "client_campaigns.md", "owner": "Client Marketing"},
    {"filename": "build_status.md", "owner": "Build & Tech"},
    {"filename": "strategic_calls.md", "owner": "Pit Wall"},
]


# Target chat name -> Paperclip receiver (to_agent, river).
# Based on Paperclip's actual agent layout:
#   CEO: axiom (orchestrator, routes cross-river strategy)
#   COO: coo_agent (operational commander)
#   CustomerAdvocate river: clint, sherry, darrell
#   AgentEmpire: wade, tammy, debra, sterling
#   CD/AIPG/AutoIntel: per-business sales/marketing/client-success specialists
#
# First cut mapping. Content-aware sub-routing (e.g. "this sales flag is for
# AI Phone Guy, route to tyler") is a v2 enhancement; for v1 the named
# receiver becomes responsible for onward routing if needed.
ROUTING_MAP: Dict[str, Dict[str, str]] = {
    "Revenue & Sales": {"to_agent": "axiom", "river": "shared"},
    "B2B Operations": {"to_agent": "clint", "river": "customer_advocate"},
    "Agent Empire": {"to_agent": "wade", "river": "agent_empire"},
    "Internal Marketing": {"to_agent": "wade", "river": "agent_empire"},
    "Sales Marketing": {"to_agent": "axiom", "river": "shared"},
    "Client Marketing": {"to_agent": "axiom", "river": "shared"},
    "Pit Wall": {"to_agent": "axiom", "river": "shared"},
    # "Build & Tech" intentionally omitted: handled in Claude Code, not Paperclip.
}

SKIPPED_TARGETS = {"Build & Tech"}

GITHUB_API_BASE = "https://api.github.com"
BRIDGE_FROM_AGENT = "cockpit_bridge"
BRIDGE_HANDOFF_TYPE = "cockpit_flag"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class BridgeConfig:
    token: str
    owner: str
    repo: str

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.owner and self.repo)


def get_bridge_config() -> Optional[BridgeConfig]:
    token = os.environ.get("GITHUB_TOKEN_TELEMETRY")
    repo_full = os.environ.get("TELEMETRY_REPO", "")
    if not token or "/" not in repo_full:
        return None
    owner, repo = repo_full.split("/", 1)
    return BridgeConfig(token=token, owner=owner, repo=repo)


# ---------------------------------------------------------------------------
# GitHub client (minimal)
# ---------------------------------------------------------------------------

def _github_headers(cfg: BridgeConfig) -> Dict[str, str]:
    return {
        "Authorization": f"token {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_file(cfg: BridgeConfig, path: str) -> Optional[Tuple[str, str]]:
    """Return (content, sha) for a file in avo-telemetry, or None on failure."""
    url = f"{GITHUB_API_BASE}/repos/{cfg.owner}/{cfg.repo}/contents/{path}"
    try:
        resp = requests.get(url, headers=_github_headers(cfg), timeout=10)
        if resp.status_code != 200:
            logger.warning("[CockpitBridge] GET %s failed: %s", path, resp.status_code)
            return None
        data = resp.json()
        content_b64 = data.get("content", "")
        sha = data.get("sha", "")
        if not content_b64 or not sha:
            return None
        content = base64.b64decode(content_b64).decode("utf-8")
        return content, sha
    except requests.RequestException as e:
        logger.warning("[CockpitBridge] GET %s errored: %s", path, e)
        return None


def _put_file(
    cfg: BridgeConfig, path: str, new_content: str, sha: str, message: str
) -> bool:
    url = f"{GITHUB_API_BASE}/repos/{cfg.owner}/{cfg.repo}/contents/{path}"
    body = {
        "message": message,
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "committer": {"name": "Paperclip Bridge", "email": "bridge@noreply.salesdroid"},
        "author": {"name": "Paperclip Bridge", "email": "bridge@noreply.salesdroid"},
    }
    try:
        resp = requests.put(url, headers=_github_headers(cfg), json=body, timeout=15)
        if resp.status_code in (200, 201):
            return True
        logger.warning(
            "[CockpitBridge] PUT %s failed: %s %s", path, resp.status_code, resp.text[:200]
        )
        return False
    except requests.RequestException as e:
        logger.warning("[CockpitBridge] PUT %s errored: %s", path, e)
        return False


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------

FLAG_HEADER_RE = re.compile(r"^🏁\s*FLAG FOR:\s*(.+?)\s*$", re.MULTILINE)
SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
FLAGS_SECTION_RE = re.compile(r"^##\s+Flags for other chats\s*$", re.MULTILINE)
FIELD_RE_TEMPLATE = r"\*\*{label}:\*\*\s*(.*)"


@dataclass
class ParsedFlag:
    source_file: str
    target: str
    what: str
    why_now: str
    by_when: str
    posted_by: str
    posted: str

    @property
    def hash(self) -> str:
        key = f"{self.source_file}|{self.target}|{self.posted}".encode("utf-8")
        return hashlib.sha256(key).hexdigest()[:32]


def _field(block: str, label: str) -> str:
    m = re.search(FIELD_RE_TEMPLATE.format(label=label), block)
    return m.group(1).strip() if m else ""


def parse_flags_from_file(source_file: str, content: str) -> List[ParsedFlag]:
    section_match = FLAGS_SECTION_RE.search(content)
    if not section_match:
        return []
    section_start = section_match.end()
    rest = content[section_start:]
    next_section = SECTION_RE.search(rest)
    section_end_in_rest = next_section.start() if next_section else len(rest)
    section_body = rest[:section_end_in_rest]

    flags: List[ParsedFlag] = []
    headers = list(FLAG_HEADER_RE.finditer(section_body))
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(section_body)
        block = section_body[start:end]
        flags.append(
            ParsedFlag(
                source_file=source_file,
                target=m.group(1).strip(),
                what=_field(block, "What"),
                why_now=_field(block, "Why now"),
                by_when=_field(block, "By when"),
                posted_by=_field(block, "Posted by"),
                posted=_field(block, "Posted"),
            )
        )
    return flags


def remove_flag_block(content: str, target: str, posted: str) -> Optional[str]:
    """Remove the exact flag block (matched by target + posted) from file content."""
    flags_section = FLAGS_SECTION_RE.search(content)
    if not flags_section:
        return None
    section_start = flags_section.end()
    rest = content[section_start:]
    next_section = SECTION_RE.search(rest)
    section_end_in_rest = next_section.start() if next_section else len(rest)
    section_body = rest[:section_end_in_rest]

    headers = list(FLAG_HEADER_RE.finditer(section_body))
    for i, m in enumerate(headers):
        if m.group(1).strip() != target.strip():
            continue
        start_in_section = m.start()
        end_in_section = (
            headers[i + 1].start() if i + 1 < len(headers) else len(section_body)
        )
        block = section_body[start_in_section:end_in_section]
        m_posted = re.search(FIELD_RE_TEMPLATE.format(label="Posted"), block)
        if not m_posted or m_posted.group(1).strip() != posted.strip():
            continue

        # Translate section-body indices back to full-content indices.
        start_in_content = section_start + start_in_section
        end_in_content = section_start + end_in_section

        before = content[:start_in_content].rstrip(" \t\n") + "\n"
        after = content[end_in_content:].lstrip("\n")
        joined = before + ("\n" + after if after else "")
        if not joined.endswith("\n"):
            joined += "\n"
        return joined
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cockpit_bridge_seen_flags (
    flag_hash TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    target TEXT NOT NULL,
    posted TEXT NOT NULL,
    handoff_id INTEGER,
    flag_closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def ensure_table() -> None:
    try:
        execute_query(CREATE_TABLE_SQL, ())
        logger.info("[CockpitBridge] cockpit_bridge_seen_flags table ready")
    except DatabaseError as e:
        logger.error("[CockpitBridge] Could not create table: %s", e)


def mark_seen(flag: ParsedFlag, handoff_id: Optional[int]) -> None:
    try:
        execute_query(
            "INSERT INTO cockpit_bridge_seen_flags "
            "(flag_hash, source_file, target, posted, handoff_id) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (flag_hash) DO NOTHING",
            (flag.hash, flag.source_file, flag.target, flag.posted, handoff_id),
        )
    except DatabaseError as e:
        logger.warning("[CockpitBridge] mark_seen failed: %s", e)


def already_seen(flag_hashes: List[str]) -> set:
    if not flag_hashes:
        return set()
    placeholders = ",".join(["%s"] * len(flag_hashes))
    try:
        rows = fetch_all(
            f"SELECT flag_hash FROM cockpit_bridge_seen_flags "
            f"WHERE flag_hash IN ({placeholders})",
            tuple(flag_hashes),
        )
        return {row[0] for row in rows}
    except DatabaseError as e:
        logger.warning("[CockpitBridge] already_seen query failed: %s", e)
        return set()


def get_completed_unclosed_bridged_handoffs() -> List[Dict[str, Any]]:
    """Find bridge-created handoffs that are complete but whose flag is not yet
    closed on avo-telemetry."""
    try:
        rows = fetch_all(
            "SELECT sf.flag_hash, sf.source_file, sf.target, sf.posted, sf.handoff_id "
            "FROM cockpit_bridge_seen_flags sf "
            "JOIN agent_handoffs ah ON ah.id = sf.handoff_id "
            "WHERE ah.status = 'complete' AND sf.flag_closed_at IS NULL",
            (),
        )
        return [
            {
                "flag_hash": r[0],
                "source_file": r[1],
                "target": r[2],
                "posted": r[3],
                "handoff_id": r[4],
            }
            for r in rows
        ]
    except DatabaseError as e:
        logger.warning("[CockpitBridge] get_completed_unclosed failed: %s", e)
        return []


def mark_flag_closed(flag_hash: str) -> None:
    try:
        execute_query(
            "UPDATE cockpit_bridge_seen_flags "
            "SET flag_closed_at = NOW() WHERE flag_hash = %s",
            (flag_hash,),
        )
    except DatabaseError as e:
        logger.warning("[CockpitBridge] mark_flag_closed failed: %s", e)


# ---------------------------------------------------------------------------
# Core poll cycles
# ---------------------------------------------------------------------------

def poll_new_flags() -> Dict[str, int]:
    """Scan all 8 telemetry files; for each new flag, create a Paperclip handoff."""
    cfg = get_bridge_config()
    if cfg is None or not cfg.enabled:
        logger.debug("[CockpitBridge] skipped poll — GITHUB_TOKEN_TELEMETRY/TELEMETRY_REPO not set")
        return {"seen": 0, "created": 0, "skipped": 0}

    all_flags: List[ParsedFlag] = []
    for entry in TELEMETRY_FILES:
        got = _get_file(cfg, entry["filename"])
        if not got:
            continue
        content, _sha = got
        all_flags.extend(parse_flags_from_file(entry["filename"], content))

    if not all_flags:
        return {"seen": 0, "created": 0, "skipped": 0}

    hashes = [f.hash for f in all_flags]
    already = already_seen(hashes)

    created = 0
    skipped = 0
    for flag in all_flags:
        if flag.hash in already:
            continue
        if flag.target in SKIPPED_TARGETS:
            mark_seen(flag, handoff_id=None)
            skipped += 1
            continue
        mapping = ROUTING_MAP.get(flag.target)
        if mapping is None:
            logger.info(
                "[CockpitBridge] No routing rule for target=%r; logging as seen but not dispatching",
                flag.target,
            )
            mark_seen(flag, handoff_id=None)
            skipped += 1
            continue

        payload = {
            "source_file": flag.source_file,
            "target": flag.target,
            "what": flag.what,
            "why_now": flag.why_now,
            "by_when": flag.by_when,
            "posted_by": flag.posted_by,
            "posted": flag.posted,
        }
        priority = "high" if flag.by_when and any(
            word in flag.by_when.lower() for word in ("immediately", "asap", "now", "today")
        ) else "medium"

        handoff_id = create_handoff(
            from_agent=BRIDGE_FROM_AGENT,
            to_agent=mapping["to_agent"],
            river=mapping["river"],
            handoff_type=BRIDGE_HANDOFF_TYPE,
            payload=payload,
            priority=priority,
        )
        if handoff_id is not None:
            mark_seen(flag, handoff_id=handoff_id)
            created += 1
            logger.info(
                "[CockpitBridge] Created handoff #%s for flag target=%s file=%s",
                handoff_id,
                flag.target,
                flag.source_file,
            )
        else:
            logger.warning(
                "[CockpitBridge] create_handoff returned None for flag target=%s posted=%s",
                flag.target,
                flag.posted,
            )

    return {"seen": len(all_flags), "created": created, "skipped": skipped}


def poll_close_completed() -> Dict[str, int]:
    """For every bridged handoff that reached 'complete', remove the flag from avo-telemetry."""
    cfg = get_bridge_config()
    if cfg is None or not cfg.enabled:
        return {"processed": 0, "closed": 0, "failed": 0}

    pending = get_completed_unclosed_bridged_handoffs()
    if not pending:
        return {"processed": 0, "closed": 0, "failed": 0}

    closed = 0
    failed = 0
    for row in pending:
        got = _get_file(cfg, row["source_file"])
        if not got:
            failed += 1
            continue
        content, sha = got
        updated = remove_flag_block(content, row["target"], row["posted"])
        if updated is None:
            # Flag may have already been removed by a chat; mark closed so we stop retrying.
            logger.info(
                "[CockpitBridge] Flag already gone in %s for target=%s; marking closed",
                row["source_file"],
                row["target"],
            )
            mark_flag_closed(row["flag_hash"])
            closed += 1
            continue

        what_preview = "agent-completed handoff"
        message = (
            f"chore(telemetry): close flag for {row['target']} from {row['source_file']}\n\n"
            f"Closed by Paperclip Bridge after handoff #{row['handoff_id']} completed. "
            f"{what_preview}"
        )
        ok = _put_file(cfg, row["source_file"], updated, sha, message)
        if ok:
            mark_flag_closed(row["flag_hash"])
            closed += 1
        else:
            failed += 1

    return {"processed": len(pending), "closed": closed, "failed": failed}


def bridge_tick() -> Dict[str, Any]:
    """Run one full bridge cycle: new flags, then complete-closures."""
    new_stats = poll_new_flags()
    close_stats = poll_close_completed()
    return {"new": new_stats, "close": close_stats}


def cleanup_for_fresh_start() -> Dict[str, Any]:
    """Idempotent recovery:
    delete any pending cockpit_bridge handoffs (likely duplicates from an earlier buggy tick)
    and wipe the seen-flags dedupe table so the next tick re-creates cleanly.

    Safe to call multiple times. Only touches rows owned by the bridge.
    """
    deleted_handoffs = -1
    deleted_seen = -1
    try:
        before = fetch_all(
            "SELECT COUNT(*) FROM agent_handoffs WHERE from_agent = %s AND status = 'pending'",
            (BRIDGE_FROM_AGENT,),
        )
        count_before = int(before[0][0]) if before else 0
        execute_query(
            "DELETE FROM agent_handoffs WHERE from_agent = %s AND status = 'pending'",
            (BRIDGE_FROM_AGENT,),
        )
        deleted_handoffs = count_before
    except DatabaseError as e:
        logger.warning("[CockpitBridge] cleanup agent_handoffs failed: %s", e)

    try:
        before = fetch_all("SELECT COUNT(*) FROM cockpit_bridge_seen_flags", ())
        count_before = int(before[0][0]) if before else 0
        execute_query("DELETE FROM cockpit_bridge_seen_flags", ())
        deleted_seen = count_before
    except DatabaseError as e:
        logger.warning("[CockpitBridge] cleanup seen table failed: %s", e)

    return {
        "pending_handoffs_deleted": deleted_handoffs,
        "seen_rows_deleted": deleted_seen,
    }


# ---------------------------------------------------------------------------
# Status for observability
# ---------------------------------------------------------------------------

def bridge_status() -> Dict[str, Any]:
    cfg = get_bridge_config()
    enabled = cfg is not None and cfg.enabled

    counts = {"total_seen": 0, "closed": 0, "open": 0}
    try:
        rows = fetch_all(
            "SELECT "
            "  COUNT(*) AS total, "
            "  COUNT(flag_closed_at) AS closed "
            "FROM cockpit_bridge_seen_flags",
            (),
        )
        if rows:
            total = rows[0][0] or 0
            closed = rows[0][1] or 0
            counts = {"total_seen": total, "closed": closed, "open": total - closed}
    except DatabaseError as e:
        logger.warning("[CockpitBridge] status query failed: %s", e)

    recent: List[Dict[str, Any]] = []
    try:
        rows = fetch_all(
            "SELECT flag_hash, source_file, target, posted, handoff_id, flag_closed_at, created_at "
            "FROM cockpit_bridge_seen_flags "
            "ORDER BY created_at DESC LIMIT 20",
            (),
        )
        for r in rows:
            recent.append({
                "flag_hash": r[0],
                "source_file": r[1],
                "target": r[2],
                "posted": r[3],
                "handoff_id": r[4],
                "flag_closed_at": str(r[5]) if r[5] else None,
                "created_at": str(r[6]),
            })
    except DatabaseError as e:
        logger.warning("[CockpitBridge] recent query failed: %s", e)

    return {
        "enabled": enabled,
        "repo": f"{cfg.owner}/{cfg.repo}" if cfg else None,
        "routing_map": ROUTING_MAP,
        "skipped_targets": sorted(SKIPPED_TARGETS),
        "counts": counts,
        "recent": recent,
    }
