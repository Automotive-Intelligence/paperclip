"""
=============================================================================
AVO COCKPIT BRIDGE — INTELLIGENT ROUTING LAYER
=============================================================================
Built: April 2026

WHY THIS EXISTS
---------------
Michael Rodriguez runs four autonomous businesses (Rivers) plus the
CustomerAdvocate build, with 24 CrewAI agents handling execution across
AI Phone Guy, Calling Digital, Automotive Intelligence, Agent Empire, and
CustomerAdvocate. The goal is a fully hands-free autonomous revenue system
where Michael can direct agent work from anywhere — including from the
dealership floor — without opening a laptop.

The AVO Cockpit is the human interface. Michael speaks a flag into his
phone. The flag gets written to avo-telemetry (a GitHub repo with 8
markdown files, one per chat context). This bridge reads those flags and
routes them to the right agent in Paperclip.

THIS IS AN OBEDIENCE SYSTEM
---------------------------
Flags do not route to categories or teams. They route to specific named
agents who are accountable for the outcome. The routing intelligence reads
the full flag content — client name, work type, urgency, business context —
and makes an accountable routing decision using GPT-4o. Every routing
decision is logged with its reasoning and stored on the handoff payload so
it can be audited.

This is not a task queue. It is an accountability layer. When a flag lands
with an agent, that agent owns the outcome. If the work is not completed
by the stated deadline, that surfaces as an execution gap.

HOW IT WORKS
------------
1. Bridge polls avo-telemetry every 60 seconds via GitHub API.
2. New flags are parsed from the "Flags for other chats" section of each
   of the 8 telemetry files.
3. Each new flag is passed to route_flag_intelligently() which calls
   GPT-4o with the full flag content plus the live agent roster and
   returns a RoutingDecision: target_agent, river, priority, confidence,
   reasoning.
4. A handoff row is created in Paperclip's agent_handoffs table targeting
   the specific named agent. The routing reasoning is embedded in the
   handoff payload under _routing so every decision is auditable.
5. The agent picks up the handoff on their next scheduled run and executes.
6. When the agent calls complete_handoff(), the bridge removes the flag
   from avo-telemetry automatically.

AGENT ACCOUNTABILITY ROSTER (24 CrewAI agents + axiom)
------------------------------------------------------
AI Phone Guy:            Alex (CEO), Zoe (Marketing), Tyler (Sales),
                         Jennifer (CS), Randy (RevOps), Joshua (Pit Wall)
Calling Digital:         Dek (CEO), Sofia (Content), Marcus (Sales),
                         Carlos (CS), Nova (Implementation), Brenda (RevOps)
Automotive Intelligence: Michael Meta (CEO), Chase (Marketing),
                         Ryan Data (Sales), Phoenix (Implementation),
                         Atlas (Research), Darrell (RevOps)
Agent Empire:            Wade (Biz Dev / de facto CEO), Debra (Producer),
                         Tammy (Community), Sterling (Web)
CustomerAdvocate:        Clint (Tech Builder), Sherry (Web Design)
Master:                  Axiom (CEO Orchestration, cross-river strategy)

Excluded from routing: michael_meta_ii (PostgreSQL inventory module) and
vera (buyer-agent negotiation class). Both are infrastructure, not
autonomous workers. They have no scheduler entry and no pending-handoff
consumer.

ROUTING INTELLIGENCE
--------------------
Routing is NOT a static lookup table. A GPT-4o call reads the full flag
content and reasons about which agent is accountable based on:
  - Business line (identified from client names, product context, keywords)
  - Work type (revenue, delivery, content, community, technical, strategy)
  - Urgency (drives priority: high / medium / low)
  - Ambiguity (surfaced via confidence score, reasoning text)

Pit Wall flags route to the CEO of the most relevant business line:
  AI Phone Guy -> Alex, Calling Digital -> Dek,
  Automotive Intelligence -> Michael Meta, Agent Empire -> Wade.
When Pit Wall flags are cross-river or strategic-meta, they route to Axiom.

Build & Tech flags are skipped (SKIPPED_TARGETS) — they stay in the Claude
Code lane. Free-text "Other..." targets that can't be routed fall back to
Axiom with a low-confidence marker so he can triage on his nightly run.

KNOWN ISSUES AND HISTORY
------------------------
v1 (April 21 2026): Shipped with a static ROUTING_MAP. Bug discovered
  where create_handoff() returned True (boolean) not an integer ID,
  causing duplicate handoffs every 60 seconds. Token blanked to stop the
  bleeding. Hotfix (PR #6) added the real RETURNING id and a
  POST /bridge/cleanup endpoint.
v2 (April 21 2026): Replaced the static ROUTING_MAP with
  route_flag_intelligently() backed by GPT-4o. Reasoning is persisted on
  each handoff payload under _routing.

ENVIRONMENT VARIABLES REQUIRED
------------------------------
GITHUB_TOKEN_TELEMETRY  — fine-grained PAT, Contents Read+Write on
                          salesdroid/avo-telemetry
TELEMETRY_REPO          — salesdroid/avo-telemetry
OPENAI_API_KEY          — used for routing intelligence (GPT-4o via
                          litellm). On routing-call failure the bridge
                          falls back to Axiom with reasoning="fallback:
                          <error>" so the flag still reaches a human-ish
                          escalation path.

OBSERVABILITY
-------------
GET  /bridge/status   — enabled state, routing mode, agent roster snapshot,
                        counts, recent 20 flags with handoff ids
POST /bridge/tick     — force one full cycle (new flags + close completed)
POST /bridge/cleanup  — one-shot recovery: purge pending bridge handoffs +
                        wipe seen-flags table. Use after a buggy tick.
Railway logs: grep for "[CockpitBridge]" and "[Routing]" to see every
decision plus its reasoning.
=============================================================================
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


# Live accountability roster. Every entry is a real CrewAI agent currently
# scheduled in Paperclip's app.py. This list is the ONLY set of valid
# to_agent values the routing intelligence may return.
#
# Excluded on purpose:
#   - michael_meta_ii: PostgreSQL dealership inventory module, not a CrewAI
#     worker (no Agent object, no scheduler entry).
#   - vera: AI-to-AI buyer negotiation class (AATA protocol), infrastructure
#     that Clint is building — not a pending-handoff consumer.
#   - coo_agent: operational commander that runs the daily accountability
#     check; receives nothing inbound from the cockpit, only audits.
AGENT_ROSTER: List[Dict[str, Any]] = [
    # AI Phone Guy
    {"name": "alex",        "river": "aiphoneguy",       "role": "CEO of The AI Phone Guy",                             "is_ceo": True},
    {"name": "zoe",         "river": "aiphoneguy",       "role": "Head of Marketing at The AI Phone Guy",               "is_ceo": False},
    {"name": "tyler",       "river": "aiphoneguy",       "role": "Senior SDR and pipeline builder at The AI Phone Guy", "is_ceo": False},
    {"name": "jennifer",    "river": "aiphoneguy",       "role": "Head of Client Success at The AI Phone Guy",          "is_ceo": False},
    {"name": "randy",       "river": "aiphoneguy",       "role": "RevOps (GoHighLevel workflow architect) at The AI Phone Guy", "is_ceo": False},
    {"name": "joshua",      "river": "aiphoneguy",       "role": "Pit Wall RevOps race engineer — reads Instantly campaign telemetry for Tyler", "is_ceo": False},
    # Calling Digital
    {"name": "dek",         "river": "callingdigital",   "role": "CEO of Calling Digital",                              "is_ceo": True},
    {"name": "sofia",       "river": "callingdigital",   "role": "Head of Content and Creative at Calling Digital",     "is_ceo": False},
    {"name": "marcus",      "river": "callingdigital",   "role": "Senior SDR and pipeline builder at Calling Digital",  "is_ceo": False},
    {"name": "carlos",      "river": "callingdigital",   "role": "Head of Client Success at Calling Digital",           "is_ceo": False},
    {"name": "nova",        "river": "callingdigital",   "role": "AI Implementation Director at Calling Digital",       "is_ceo": False},
    {"name": "brenda",      "river": "callingdigital",   "role": "RevOps (Attio workflow architect) at Calling Digital","is_ceo": False},
    # Automotive Intelligence
    {"name": "michael_meta","river": "autointelligence", "role": "CEO of Automotive Intelligence",                      "is_ceo": True},
    {"name": "chase",       "river": "autointelligence", "role": "Head of Marketing at Automotive Intelligence",        "is_ceo": False},
    {"name": "ryan_data",   "river": "autointelligence", "role": "Senior SDR and pipeline builder at Automotive Intelligence","is_ceo": False},
    {"name": "phoenix",     "river": "autointelligence", "role": "Head of Implementation at Automotive Intelligence",   "is_ceo": False},
    {"name": "atlas",       "river": "autointelligence", "role": "Research Analyst (dealer briefs) at Automotive Intelligence", "is_ceo": False},
    {"name": "darrell",     "river": "autointelligence", "role": "RevOps (HubSpot workflow architect) at Automotive Intelligence", "is_ceo": False},
    # Agent Empire
    {"name": "wade",        "river": "agent_empire",     "role": "Biz Dev (sponsor outreach) and de facto lead at Agent Empire", "is_ceo": True},
    {"name": "debra",       "river": "agent_empire",     "role": "Producer at Agent Empire (episodes, calendars, outlines)",    "is_ceo": False},
    {"name": "tammy",       "river": "agent_empire",     "role": "Community agent at Agent Empire (Skool engagement)",  "is_ceo": False},
    {"name": "sterling",    "river": "agent_empire",     "role": "Web agent at Agent Empire (buildagentempire.com builder/maintainer)", "is_ceo": False},
    # CustomerAdvocate
    {"name": "clint",       "river": "customer_advocate","role": "Technical Builder at CustomerAdvocate (VERA scoring + AATA protocol)", "is_ceo": False},
    {"name": "sherry",      "river": "customer_advocate","role": "Web Design agent at CustomerAdvocate",                "is_ceo": False},
    # Master
    {"name": "axiom",       "river": "shared",           "role": "Master CEO orchestration; cross-river strategy and fallback target", "is_ceo": True},
]

# Per-river CEO, used when Pit Wall flags reference a specific business.
RIVER_CEO: Dict[str, str] = {
    "aiphoneguy": "alex",
    "callingdigital": "dek",
    "autointelligence": "michael_meta",
    "agent_empire": "wade",
    "customer_advocate": "axiom",  # no river CEO; master CEO handles it
    "shared": "axiom",
}

VALID_AGENTS = {a["name"] for a in AGENT_ROSTER}
VALID_RIVERS = {a["river"] for a in AGENT_ROSTER}

SKIPPED_TARGETS = {"Build & Tech"}

ROUTING_MODEL = os.environ.get("BRIDGE_ROUTING_MODEL", "gpt-4o")

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
# Intelligent routing (GPT-4o)
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    target_agent: str
    river: str
    priority: str  # "high" | "medium" | "low"
    confidence: float  # 0.0 - 1.0
    reasoning: str
    source: str  # "intelligent" | "fallback"


def _routing_system_prompt() -> str:
    roster_lines: List[str] = []
    for a in AGENT_ROSTER:
        ceo_tag = " [CEO]" if a["is_ceo"] else ""
        roster_lines.append(f"- {a['name']} (river: {a['river']}){ceo_tag} — {a['role']}")
    roster_block = "\n".join(roster_lines)

    river_ceo_block = "\n".join(
        f"- {river}: {ceo}" for river, ceo in RIVER_CEO.items()
    )

    return f"""You are the routing intelligence for AVO Cockpit. You receive a handoff flag written by Michael (operator of AI Phone Guy, Calling Digital, Automotive Intelligence, Agent Empire, and the CustomerAdvocate build) and you assign it to the ONE accountable agent in Paperclip who should own the outcome.

This is an obedience system, not a task queue. Flags route to specific named agents. When a flag lands with an agent, that agent owns the outcome; deadline misses surface as execution gaps. Be willing to commit to a name.

Live agent roster (the ONLY valid values for target_agent):
{roster_block}

Per-river CEOs (used when the flag is Pit Wall / strategic / cross-river, or when ambiguity forces escalation):
{river_ceo_block}

How to reason:
1. Identify the business line. Look for client names, product names, keywords. Map:
   - "AI Phone Guy", "AIPG", "theaiphoneguy.ai", outbound-call automation -> aiphoneguy
   - "Calling Digital", "CD", "CD client", monthly GA reports for small biz, Worden Welding, Garrett (Worden), Ryan Velazquez, Book'd -> callingdigital
   - "Automotive Intelligence", "AI Intel", dealer/dealership work, HubSpot dealer briefs -> autointelligence
   - "Agent Empire", "AE", "Skool", YouTube episodes, sponsor outreach, community -> agent_empire
   - VERA, AATA, consumer-buyer agent, "The Architect" protocol -> customer_advocate
2. Identify the work type: revenue/sales, client delivery/CS, marketing/content, RevOps/workflow, research/analysis, implementation/build, community, strategy.
3. Pick the accountable agent from that (river, work type) pair.
4. Pit Wall flags: if a specific business is referenced, route to that river's CEO. If cross-river / meta / strategic with no clear business, route to axiom.
5. If the flag is genuinely ambiguous, pick your best guess and set confidence accordingly; axiom is the safe fallback target (set reasoning to explain the escalation).
6. Priority: "high" if the by_when field contains urgency cues (immediately, ASAP, now, today, EOD, this morning) or a same-day deadline; "low" only if the flag is explicitly deprioritized; else "medium".

Output JSON only, exactly this shape:
{{
  "target_agent": "<one of the names above>",
  "river": "<the river for that agent>",
  "priority": "high|medium|low",
  "confidence": 0.0-1.0,
  "reasoning": "<one or two short sentences explaining the decision>"
}}"""


def _render_flag_for_prompt(flag: ParsedFlag) -> str:
    parts = [
        f"Source file: {flag.source_file}",
        f"Source chat (posted by): {flag.posted_by}",
        f"Target chat on the flag: {flag.target}",
        f"What: {flag.what}",
    ]
    if flag.why_now:
        parts.append(f"Why now: {flag.why_now}")
    if flag.by_when:
        parts.append(f"By when: {flag.by_when}")
    parts.append(f"Posted at: {flag.posted}")
    return "\n".join(parts)


def _fallback_decision(flag: ParsedFlag, reason: str) -> RoutingDecision:
    priority = "high" if flag.by_when and any(
        w in flag.by_when.lower() for w in ("immediately", "asap", "now", "today", "eod")
    ) else "medium"
    return RoutingDecision(
        target_agent="axiom",
        river="shared",
        priority=priority,
        confidence=0.2,
        reasoning=f"fallback: {reason}",
        source="fallback",
    )


def route_flag_intelligently(flag: ParsedFlag) -> RoutingDecision:
    """GPT-4o-backed routing. Returns a RoutingDecision. Falls back to axiom
    with a fallback reasoning string on any failure so the flag still reaches
    an accountable owner."""
    if not os.environ.get("OPENAI_API_KEY"):
        return _fallback_decision(flag, "OPENAI_API_KEY not set")

    try:
        from litellm import completion  # type: ignore
    except Exception as e:
        return _fallback_decision(flag, f"litellm import failed: {e}")

    try:
        response = completion(
            model=ROUTING_MODEL,
            messages=[
                {"role": "system", "content": _routing_system_prompt()},
                {"role": "user", "content": _render_flag_for_prompt(flag)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=30,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("[Routing] completion failed for target=%s posted=%s: %s",
                       flag.target, flag.posted, e)
        return _fallback_decision(flag, f"completion error: {e}")

    try:
        parsed = json.loads(content)
    except Exception as e:
        logger.warning("[Routing] invalid JSON for target=%s: %s | raw=%s",
                       flag.target, e, content[:300])
        return _fallback_decision(flag, f"invalid JSON: {e}")

    target_agent = str(parsed.get("target_agent", "")).strip()
    river = str(parsed.get("river", "")).strip()
    priority = str(parsed.get("priority", "medium")).strip().lower()
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(parsed.get("reasoning", "")).strip() or "no reasoning provided"

    if target_agent not in VALID_AGENTS:
        return _fallback_decision(
            flag,
            f"model picked unknown agent {target_agent!r}; original reasoning: {reasoning}",
        )

    # Trust the roster's river for the agent (keeps to_agent/river coherent
    # even if the model disagrees on the river name).
    canonical_river = next((a["river"] for a in AGENT_ROSTER if a["name"] == target_agent), river)

    return RoutingDecision(
        target_agent=target_agent,
        river=canonical_river,
        priority=priority,
        confidence=confidence,
        reasoning=reasoning,
        source="intelligent",
    )


# ---------------------------------------------------------------------------
# Core poll cycles
# ---------------------------------------------------------------------------

def poll_new_flags() -> Dict[str, int]:
    """Scan all 8 telemetry files; for each new flag, route it intelligently
    and create a Paperclip handoff."""
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

        decision = route_flag_intelligently(flag)
        logger.info(
            "[Routing] flag target=%s -> agent=%s river=%s priority=%s confidence=%.2f source=%s reasoning=%s",
            flag.target, decision.target_agent, decision.river,
            decision.priority, decision.confidence, decision.source, decision.reasoning,
        )

        payload = {
            "source_file": flag.source_file,
            "target": flag.target,
            "what": flag.what,
            "why_now": flag.why_now,
            "by_when": flag.by_when,
            "posted_by": flag.posted_by,
            "posted": flag.posted,
            "_routing": {
                "target_agent": decision.target_agent,
                "river": decision.river,
                "priority": decision.priority,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "source": decision.source,
                "model": ROUTING_MODEL if decision.source == "intelligent" else None,
            },
        }

        handoff_id = create_handoff(
            from_agent=BRIDGE_FROM_AGENT,
            to_agent=decision.target_agent,
            river=decision.river,
            handoff_type=BRIDGE_HANDOFF_TYPE,
            payload=payload,
            priority=decision.priority,
        )
        if handoff_id is not None:
            mark_seen(flag, handoff_id=handoff_id)
            created += 1
            logger.info(
                "[CockpitBridge] Created handoff #%s for flag target=%s file=%s -> %s",
                handoff_id, flag.target, flag.source_file, decision.target_agent,
            )
        else:
            logger.warning(
                "[CockpitBridge] create_handoff returned None for flag target=%s posted=%s",
                flag.target, flag.posted,
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
            "SELECT sf.flag_hash, sf.source_file, sf.target, sf.posted, "
            "       sf.handoff_id, sf.flag_closed_at, sf.created_at, "
            "       ah.to_agent, ah.river, ah.priority, ah.status, ah.payload "
            "FROM cockpit_bridge_seen_flags sf "
            "LEFT JOIN agent_handoffs ah ON ah.id = sf.handoff_id "
            "ORDER BY sf.created_at DESC LIMIT 20",
            (),
        )
        for r in rows:
            routing_info: Optional[Dict[str, Any]] = None
            payload_raw = r[11]
            if payload_raw:
                try:
                    payload_dict = (
                        payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw)
                    )
                    routing_info = payload_dict.get("_routing")
                except Exception:
                    routing_info = None
            recent.append({
                "flag_hash": r[0],
                "source_file": r[1],
                "target": r[2],
                "posted": r[3],
                "handoff_id": r[4],
                "flag_closed_at": str(r[5]) if r[5] else None,
                "created_at": str(r[6]),
                "to_agent": r[7],
                "river": r[8],
                "priority": r[9],
                "handoff_status": r[10],
                "routing": routing_info,
            })
    except DatabaseError as e:
        logger.warning("[CockpitBridge] recent query failed: %s", e)

    routing_mode = "intelligent" if os.environ.get("OPENAI_API_KEY") else "fallback-only"

    return {
        "enabled": enabled,
        "repo": f"{cfg.owner}/{cfg.repo}" if cfg else None,
        "routing_mode": routing_mode,
        "routing_model": ROUTING_MODEL,
        "agent_roster": [
            {"name": a["name"], "river": a["river"], "is_ceo": a["is_ceo"], "role": a["role"]}
            for a in AGENT_ROSTER
        ],
        "river_ceo": RIVER_CEO,
        "skipped_targets": sorted(SKIPPED_TARGETS),
        "counts": counts,
        "recent": recent,
    }
