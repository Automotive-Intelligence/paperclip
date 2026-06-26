"""Flag Responder — autonomous draft producer for flags routed by flag_router.

When the flag_router routes a 🏁 FLAG FOR: <X> to seat X:
  1. Posts a Slack notification ping (already wired)
  2. Enqueues a call here to produce a DRAFT response

This module:
  - Loads X's persona prompt from services/personas/<slug>.md (mirrored from
    avo-slack so the bot's identity is consistent across surfaces)
  - Loads X's bounded-authority block from authority.yaml in avo-telemetry
  - Builds a prompt: persona + flag context + authority + draft-only directive
  - Calls the LLM (OpenRouter Gemini Flash — cheap + broad-context)
  - Runs LLM-as-judge ("would Michael ship this verbatim?")
  - Posts the draft as a *threaded reply* on the original Slack ping
  - Records to persona_runs (Postgres audit trail) for cost + outcome tracking

v1 contract: every seat is `mode: draft_only`. The responder never opens
PRs, sends email, publishes to channels, or modifies live state. Drafts are
posted to Slack for Michael to approve, redirect, or take over.

Distinct from the older `persona_executor.py` (APE), which pulls flags from
the `agent_handoffs` table and ships via email with audit envelopes + an
adversarial reviewer. APE is the higher-authority pipeline (Infrastructure
persona, runs commands). flag_responder is the lower-authority Slack
draft-producer that fires on every avo-telemetry flag.

Cost shape: Gemini Flash via OpenRouter (~$0.10/1M input, ~$0.40/1M output).
A typical run is ~3k input + 1k output ≈ $0.0007. 200k daily output-token
cap per seat ≈ $0.08/seat/day worst case.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests
import yaml

from services.database import execute_query, fetch_all
from services.flag_router import FlagBlock, Seat, _fetch_telemetry_path

logger = logging.getLogger(__name__)


# ── Config + lookup helpers ────────────────────────────────────────────────


_PERSONA_DIR = Path(__file__).resolve().parent / "personas"

# canonical_name → persona file slug (mirrors avo-slack/channels.yaml mapping)
_SEAT_TO_SLUG: dict[str, str] = {
    "CMO":                "cmo",
    "Internal Marketing": "marketing_internal",
    "Revenue & Sales":    "revenue_sales",
    "Sales Marketing":    "marketing_internal",
    "Client Marketing":   "client_marketing_garage",
    "B2B Operations":     "b2b_operations",
    "Agent Empire":       "agent_empire",
    "Build & Tech":       "build_tech",
    "Pit Wall":           "pit_wall",
    "WEND Build":         "wend_build",
}


_REQUEST_TIMEOUT = 60
_SLACK_API = "https://slack.com/api/chat.postMessage"
_AUTHORITY_CACHE: dict | None = None


def _load_authority(force_refresh: bool = False) -> dict:
    """Fetch authority.yaml from avo-telemetry main. Cached in-process."""
    global _AUTHORITY_CACHE
    if _AUTHORITY_CACHE is not None and not force_refresh:
        return _AUTHORITY_CACHE
    try:
        text = _fetch_telemetry_path("authority.yaml")
        _AUTHORITY_CACHE = yaml.safe_load(text) or {}
    except Exception as e:
        logger.error("[flag_responder] authority.yaml fetch failed: %s", e)
        _AUTHORITY_CACHE = {"defaults": {"mode": "draft_only"}, "seats": []}
    return _AUTHORITY_CACHE


def _authority_for(seat_name: str) -> dict:
    a = _load_authority()
    for s in a.get("seats", []) or []:
        if s.get("canonical_name") == seat_name:
            return {**(a.get("defaults") or {}), **s}
    return dict(a.get("defaults") or {"mode": "draft_only"})


def _load_persona_prompt(seat_name: str) -> str:
    """Read the persona prompt for this seat. Returns "" if unknown."""
    slug = _SEAT_TO_SLUG.get(seat_name)
    if not slug:
        return ""
    path = _PERSONA_DIR / f"{slug}.md"
    if not path.exists():
        logger.warning("[flag_responder] persona file missing: %s", path)
        return ""
    return path.read_text()


# ── persona_runs audit table ───────────────────────────────────────────────


_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS persona_runs (
    id              SERIAL PRIMARY KEY,
    seat            TEXT NOT NULL,
    flag_source     TEXT NOT NULL,
    flag_posted_ts  TEXT NOT NULL,
    flag_target_raw TEXT,
    status          TEXT NOT NULL,
    judge_verdict   TEXT,
    judge_reason    TEXT,
    draft_chars     INT,
    tokens_input    INT,
    tokens_output   INT,
    slack_ts        TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (seat, flag_source, flag_posted_ts)
)
"""

# Idempotent migration: add body_hash + is_escalation, swap UNIQUE so each
# escalation (same identity, different body) re-spawns one draft.
_TABLE_MIGRATIONS = [
    "ALTER TABLE persona_runs ADD COLUMN IF NOT EXISTS body_hash TEXT",
    "ALTER TABLE persona_runs ADD COLUMN IF NOT EXISTS is_escalation BOOLEAN NOT NULL DEFAULT FALSE",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS persona_runs_unique_v2_idx "
        "ON persona_runs (seat, flag_source, flag_posted_ts, COALESCE(body_hash, ''))"
    ),
    # Drop the v1 UNIQUE on (seat, flag_source, flag_posted_ts). Mirrors the
    # routed_flags drop — same ON CONFLICT swallow bug applies here too.
    "ALTER TABLE persona_runs DROP CONSTRAINT IF EXISTS persona_runs_seat_flag_source_flag_posted_ts_key",
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
            logger.warning("[flag_responder] migration step skipped: %s (%s)", ddl[:60], e)
    _table_ensured = True


def _already_ran(seat: str, flag: FlagBlock) -> bool:
    """True if we've already produced a draft for this exact (seat, flag-identity,
    body_hash) tuple. Same identity with a different body (an escalation) does
    NOT match — the responder re-runs to produce a fresh draft for the new body.
    """
    _ensure_table()
    rows = fetch_all(
        """
        SELECT 1 FROM persona_runs
        WHERE seat=%s AND flag_source=%s AND flag_posted_ts=%s
          AND COALESCE(body_hash, '') = %s
        LIMIT 1
        """,
        (seat, flag.source_file, flag.posted_ts, flag.body_hash),
    )
    return bool(rows)


def _record_run(
    seat: str,
    flag: FlagBlock,
    status: str,
    *,
    judge_verdict: str | None = None,
    judge_reason: str | None = None,
    draft_chars: int | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    slack_ts: str | None = None,
    error: str | None = None,
    is_escalation: bool = False,
) -> None:
    _ensure_table()
    execute_query(
        """
        INSERT INTO persona_runs
            (seat, flag_source, flag_posted_ts, flag_target_raw, status,
             judge_verdict, judge_reason, draft_chars,
             tokens_input, tokens_output, slack_ts, error,
             body_hash, is_escalation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            seat, flag.source_file, flag.posted_ts, flag.target_raw, status,
            judge_verdict, judge_reason, draft_chars,
            tokens_input, tokens_output, slack_ts, error,
            flag.body_hash, is_escalation,
        ),
    )


def _tokens_today(seat: str) -> int:
    """Sum output tokens for this seat across today (UTC). Used for daily cap."""
    _ensure_table()
    rows = fetch_all(
        """
        SELECT COALESCE(SUM(tokens_output), 0)
        FROM persona_runs
        WHERE seat=%s AND created_at >= DATE_TRUNC('day', NOW())
        """,
        (seat,),
    )
    if not rows:
        return 0
    return int(rows[0][0] or 0)


# ── LLM call ───────────────────────────────────────────────────────────────


# Models the flag-responder refuses to call. The responder fires on every
# routed flag, so an expensive model here = a runaway cost vector. Block
# the obvious price tiers; the env var must be set to something cheap.
# (OpenRouter can serve Anthropic + premium OpenAI — without this guard a
# typo in FLAG_RESPONDER_MODEL would silently 30x our cost per draft.)
_DENYLIST_PREFIXES = (
    "anthropic/", "claude-",      # Claude — that's what Claude Max is for
    "openai/gpt-4", "openai/o1",  # premium OpenAI tiers
    "openai/o3",
)


def _llm_call(prompt: str, max_tokens: int = 2000) -> tuple[str, int, int]:
    """Call OpenRouter with the configured cheap model. Returns (text, t_in, t_out).

    Refuses anything in _DENYLIST_PREFIXES so a misconfigured env var can't
    silently route every flag draft through Claude / GPT-4 / o1 / o3. Raises
    RuntimeError on a denied model; caller records llm_error and skips.
    """
    api_key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY missing for flag responder")
    model = os.getenv("FLAG_RESPONDER_MODEL", "google/gemini-2.5-flash").strip()
    model_lower = model.lower()
    if any(model_lower.startswith(p) for p in _DENYLIST_PREFIXES):
        raise RuntimeError(
            f"flag_responder refuses model {model!r} — premium tier denied "
            "(see _DENYLIST_PREFIXES). Set FLAG_RESPONDER_MODEL to a cheap model "
            "(google/gemini-2.5-flash, deepseek/deepseek-chat, etc.)."
        )
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://paperclip-production-ba14.up.railway.app",
            "X-Title": "AVO Flag Responder",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.4,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    text = (body["choices"][0]["message"]["content"] or "").strip()
    usage = body.get("usage") or {}
    return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


# ── Prompt construction ───────────────────────────────────────────────────


def _build_executor_prompt(
    persona: str,
    flag: FlagBlock,
    authority: dict,
    is_escalation: bool = False,
) -> str:
    can = "\n".join(f"  - {x}" for x in (authority.get("can_draft") or []))
    cannot = "\n".join(f"  - {x}" for x in (authority.get("cannot") or []))
    mode = authority.get("mode", "draft_only")
    escalation_note = ""
    if is_escalation:
        escalation_note = (
            "\n*** ESCALATED FLAG ***\n"
            "This flag was already routed once. The sender updated its body\n"
            "(escalation marker, scope change, or status update). Your previous\n"
            "draft is stale. Re-read the fields below; if your prior take still\n"
            "holds, say so briefly and call out what changed. If the change\n"
            "shifts the work, produce a fresh draft.\n"
        )
    return f"""{persona}

---
{escalation_note}
A flag was just routed to you via the avo-telemetry flag-router:

  Target:    {flag.target_raw}
  What:      {flag.what}
  Why now:   {flag.why_now}
  By when:   {flag.by_when}
  From:      {flag.posted_by}
  Source:    {flag.source_file}:{flag.line_no}

---

Your authority (v1 mode: {mode}):

  You CAN produce:
{can or "  - (defaults; see authority.yaml)"}

  You CANNOT (must defer to Michael):
{cannot or "  - (defaults; see authority.yaml)"}

---

Produce a draft response that takes the first crack at this work. Your output
will be posted as a Slack thread reply under the original flag notification.
Michael will read it and approve, redirect, or take over.

Aim for, in this order:
  1. Concrete next steps — what gets done, in what order
  2. Paste-ready artifacts when applicable (code, copy, URLs, queries)
  3. Specific blockers + open questions, if any
  4. Effort estimate + when this could realistically ship

Format constraints:
  - Under 1200 characters total. Slack limit is hard; longer is truncated.
  - No em-dashes (per outbound copy rule).
  - Terse operator voice. State the move. No "I think" / "perhaps" / filler.
  - Markdown OK; code blocks for actual code.
"""


_JUDGE_PROMPT = """You are reviewing a draft produced by an autonomous AVO
persona run. The draft will be posted to Slack as a thread reply for Michael
to read.

Original flag (what was asked of the seat):
  Target:    {target_raw}
  What:      {what}
  Why now:   {why_now}
  From:      {posted_by}

Draft produced by the persona:
\"\"\"
{draft}
\"\"\"

Judge whether Michael would ship this verbatim. Return JSON only:

  {{"verdict": "ship_as_is" | "ship_with_note" | "revise",
    "reason": "<one short sentence; required if not ship_as_is>"}}

Rules:
  - ship_as_is: concrete, on-voice, specific to the flag, no fabricated claims
  - ship_with_note: ship, but Michael should see a specific concern first
  - revise: missing the ask, off-voice, vague, or fabricates facts/numbers
"""


def _judge(flag: FlagBlock, draft: str) -> tuple[str, str]:
    """LLM-as-judge over the draft. Returns (verdict, reason).

    Defaults to ('ship_with_note', 'judge_unavailable') on any failure so we
    still post — silence is worse than a soft warning.
    """
    try:
        prompt = _JUDGE_PROMPT.format(
            target_raw=flag.target_raw,
            what=flag.what[:500],
            why_now=flag.why_now[:300],
            posted_by=flag.posted_by,
            draft=draft[:2000],
        )
        text, _, _ = _llm_call(prompt, max_tokens=200)
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return "ship_with_note", "judge_parse_fail"
        obj = json.loads(text[start : end + 1])
        return str(obj.get("verdict", "ship_with_note")), str(obj.get("reason", ""))
    except Exception as e:
        logger.warning("[flag_responder] judge failed: %s", e)
        return "ship_with_note", f"judge_error: {type(e).__name__}"


# ── Slack thread reply ────────────────────────────────────────────────────


def _post_thread_reply(
    channel: str,
    thread_ts: str,
    draft: str,
    judge_verdict: str,
    judge_reason: str,
) -> tuple[bool, str | None]:
    """Post the draft as a threaded reply on the original flag message.

    Returns (ok, reply_ts).
    """
    token = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if not token:
        return False, None
    header = ":robot_face: *Flag Responder draft* (review and approve, redirect, or take over)"
    if judge_verdict == "revise":
        header += f"\n:warning: judge flagged: {judge_reason}"
    elif judge_verdict == "ship_with_note":
        header += f"\n:eyes: heads-up: {judge_reason}"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": draft[:2900]}},
    ]
    try:
        r = requests.post(
            _SLACK_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": channel,
                "thread_ts": thread_ts,
                "text": "Flag Responder draft for the flag above",
                "blocks": blocks,
                "unfurl_links": False,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and body.get("ok"):
            return True, body.get("ts")
        logger.error(
            "[flag_responder] thread reply failed: http=%s body=%s",
            r.status_code, body,
        )
        return False, None
    except Exception as e:
        logger.error("[flag_responder] thread reply raised: %s", e)
        return False, None


# ── Public entry ───────────────────────────────────────────────────────────


def respond_to_flag(
    seat: Seat,
    flag: FlagBlock,
    slack_channel_id: str,
    slack_thread_ts: str,
    is_escalation: bool = False,
) -> dict:
    """Run the responder for a single (seat, flag). Returns audit summary.

    Idempotent via persona_runs UNIQUE(seat, flag_source, flag_posted_ts,
    body_hash). Same flag with the same body never re-spawns. Same flag with
    a NEW body (escalation) DOES re-spawn — the work changed; the draft is
    stale.
    """
    seat_name = seat.canonical_name
    summary = {
        "seat": seat_name,
        "flag_source": flag.source_file,
        "flag_posted_ts": flag.posted_ts,
        "is_escalation": is_escalation,
        "status": "started",
    }

    if _already_ran(seat_name, flag):
        summary["status"] = "skipped_duplicate"
        return summary

    persona = _load_persona_prompt(seat_name)
    if not persona:
        summary["status"] = "no_persona"
        _record_run(seat_name, flag, "no_persona",
                    error=f"no persona for seat {seat_name}",
                    is_escalation=is_escalation)
        return summary

    authority = _authority_for(seat_name)
    cap = int(authority.get("daily_token_cap", 200000))
    spent = _tokens_today(seat_name)
    if spent >= cap:
        summary["status"] = "cap_exceeded"
        _record_run(
            seat_name, flag, "cap_exceeded",
            tokens_output=0,
            error=f"daily cap {cap} reached ({spent} tokens already out)",
            is_escalation=is_escalation,
        )
        return summary

    prompt = _build_executor_prompt(persona, flag, authority, is_escalation=is_escalation)
    try:
        draft, t_in, t_out = _llm_call(prompt, max_tokens=2000)
    except Exception as e:
        summary["status"] = "llm_error"
        _record_run(seat_name, flag, "llm_error",
                    error=f"{type(e).__name__}: {e}",
                    is_escalation=is_escalation)
        return summary

    if not draft.strip():
        _record_run(
            seat_name, flag, "empty_draft",
            tokens_input=t_in, tokens_output=t_out,
            error="LLM returned empty content",
            is_escalation=is_escalation,
        )
        summary["status"] = "empty_draft"
        return summary

    verdict, reason = "ship_as_is", ""
    if authority.get("judge_required", True):
        verdict, reason = _judge(flag, draft)

    ok, reply_ts = _post_thread_reply(
        slack_channel_id, slack_thread_ts, draft, verdict, reason,
    )
    status = "posted" if ok else "slack_fail"
    _record_run(
        seat_name, flag, status,
        judge_verdict=verdict,
        judge_reason=reason,
        draft_chars=len(draft),
        tokens_input=t_in,
        tokens_output=t_out,
        slack_ts=reply_ts,
        is_escalation=is_escalation,
    )
    summary.update({
        "status": status,
        "judge_verdict": verdict,
        "tokens_input": t_in,
        "tokens_output": t_out,
        "draft_chars": len(draft),
    })
    return summary
