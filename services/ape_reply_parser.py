# services/ape_reply_parser.py
"""Resend inbound webhook handler — parses Michael's reply to an APE
audit email and executes the corresponding action."""

import json
import logging
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


KNOWN_ACTIONS = ("REVERT", "PAUSE", "PAUSE ALL", "ASK", "NOTES", "HELP")
ALLOWED_UNDO_COMMANDS = ("git", "doppler", "railway")
SHELL_METACHARS = (";", "|", "&", "`", "$(", ">", "<", "\n")


def classify_reply(body: str) -> tuple[str, str]:
    """Return (action, args) by reading the first word of the reply.

    Strips quoted prior message + email signatures.
    """
    if not body:
        return ("UNKNOWN", "")
    # Strip Gmail/Outlook style quote chevrons + everything after "On <date>... wrote:"
    cleaned = re.split(r"\n\s*On\s+.+wrote:", body, flags=re.IGNORECASE)[0]
    cleaned = "\n".join(line for line in cleaned.splitlines() if not line.lstrip().startswith(">"))
    cleaned = cleaned.strip()
    if not cleaned:
        return ("UNKNOWN", "")

    # Try multi-word actions first
    upper_start = cleaned.upper()
    if upper_start.startswith("PAUSE ALL"):
        return ("PAUSE_ALL", cleaned[len("PAUSE ALL"):].strip())
    for action in ("REVERT", "PAUSE", "ASK", "NOTES", "HELP"):
        if upper_start.startswith(action):
            return (action, cleaned[len(action):].strip())
    return ("UNKNOWN", cleaned[:200])


def extract_ship_id(headers: Dict[str, Any], body: str) -> Optional[str]:
    """Try header first, then grep body for 'Ship ID: <id>'."""
    if headers:
        for k, v in headers.items():
            if k.lower() == "x-ape-ship-id":
                return str(v).strip()
    match = re.search(r"Ship ID:\s*([a-zA-Z0-9-]+)", body)
    return match.group(1) if match else None


def lookup_ship_persona_and_envelope(ship_id: str) -> tuple[Optional[str], Optional[Dict]]:
    try:
        from services.database import fetch_all
        rows = fetch_all(
            "SELECT target, ape_audit_envelope FROM agent_handoffs WHERE ape_session_id = %s LIMIT 1",
            (ship_id,),
        )
        if not rows:
            return (None, None)
        persona, env_jsonb = rows[0]
        env = env_jsonb if isinstance(env_jsonb, dict) else (json.loads(env_jsonb) if env_jsonb else None)
        return (persona, env)
    except Exception as e:
        logger.warning(f"[ape:reply] ship lookup failed: {e}")
        return (None, None)


def execute_revert(ship_id: str, undo_command: str) -> str:
    """Run the stored undo command with allowlist + no shell.

    Hard security rule: undo_command originates from an AI-generated audit
    envelope and could be malicious. We require:
      - First token is one of ALLOWED_UNDO_COMMANDS (git, doppler, railway)
      - No raw shell metacharacters in the string (no ;|&`$(><newlines)
      - Executed via subprocess.run with shell=False (no bash -c)

    Returns a plain-English status message.
    """
    if not undo_command or undo_command.strip().lower() in ("noop", ""):
        return f"No undo command stored for ship {ship_id}"
    raw = undo_command.strip()
    if any(meta in raw for meta in SHELL_METACHARS):
        return (
            f"REVERT REJECTED — undo_command contains shell metacharacter(s); "
            f"refusing to execute for ship {ship_id}"
        )
    try:
        parsed = shlex.split(raw)
    except ValueError as e:
        return f"REVERT REJECTED — shlex parse error: {e}"
    if not parsed:
        return f"REVERT REJECTED — empty parsed command for ship {ship_id}"
    if parsed[0] not in ALLOWED_UNDO_COMMANDS:
        return (
            f"REVERT REJECTED — first token '{parsed[0]}' not in "
            f"allowlist {ALLOWED_UNDO_COMMANDS} for ship {ship_id}"
        )
    try:
        result = subprocess.run(
            parsed,
            cwd=os.path.expanduser("~/avo-telemetry"),
            capture_output=True,
            timeout=120,
            text=True,
            shell=False,
        )
        ok = result.returncode == 0
        return (
            f"REVERT {'succeeded' if ok else 'FAILED'} (exit {result.returncode})\n"
            f"command: {' '.join(parsed)}\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )
    except Exception as e:
        return f"REVERT errored: {e}"


def write_pause(persona: str, ship_id: str, hours: int = 24) -> str:
    try:
        import psycopg2
        from services.database import _get_url
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        conn = psycopg2.connect(_get_url())
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO persona_executor_pause (persona, paused_until, reason, triggered_by_ship_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (persona) DO UPDATE
            SET paused_until = EXCLUDED.paused_until,
                reason = EXCLUDED.reason,
                triggered_by_ship_id = EXCLUDED.triggered_by_ship_id
            """,
            (persona, until, f"Reply-driven pause from ship {ship_id}", ship_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        return f"{persona} autopilot paused until {until.isoformat(timespec='seconds')}"
    except Exception as e:
        return f"Pause write failed: {e}"


def log_reply_telemetry(ship_id: str, reply_text: str, action: str) -> None:
    try:
        import psycopg2
        from services.database import _get_url
        conn = psycopg2.connect(_get_url())
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ape_reply_telemetry (ship_id, reply_text, reply_action) VALUES (%s, %s, %s)",
            (ship_id, reply_text[:2000], action),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"[ape:reply] telemetry insert failed: {e}")


def handle_inbound(body: str, headers: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level inbound handler. Returns a result dict for the webhook response."""
    ship_id = extract_ship_id(headers, body)
    action, args = classify_reply(body)
    log_reply_telemetry(ship_id or "unknown", body, action)

    if action == "UNKNOWN":
        return {"action": "UNKNOWN", "message": "Reply didn't match a known action."}

    if action == "HELP":
        help_text = (
            "APE reply actions:\n"
            "  REVERT       — undo this ship\n"
            "  PAUSE        — disable this persona's autopilot for 24h\n"
            "  PAUSE ALL    — disable all personas for 24h\n"
            "  ASK <text>   — ask the AI a question about this ship\n"
            "  NOTES <text> — log a note against this ship (no action)\n"
            "  HELP         — this message\n"
        )
        return {"action": "HELP", "message": help_text}

    if not ship_id and action not in ("PAUSE_ALL",):
        return {"action": action, "message": "Couldn't find Ship ID in reply."}

    if action == "PAUSE_ALL":
        return {"action": "PAUSE_ALL", "message": write_pause("*", ship_id or "global")}

    persona, envelope = lookup_ship_persona_and_envelope(ship_id) if ship_id else (None, None)

    if action == "REVERT":
        if not envelope:
            return {"action": "REVERT", "message": f"No envelope found for ship {ship_id}"}
        return {"action": "REVERT", "message": execute_revert(ship_id, envelope.get("undo_command", ""))}

    if action == "PAUSE":
        if not persona:
            return {"action": "PAUSE", "message": "Couldn't determine persona to pause"}
        return {"action": "PAUSE", "message": write_pause(persona, ship_id)}

    if action == "ASK":
        # Post a flag back to the persona for follow-up. Phase 1 stub.
        return {"action": "ASK", "message": f"ASK logged (will post follow-up flag in v2)", "question": args}

    if action == "NOTES":
        # Already logged via telemetry. No further action.
        return {"action": "NOTES", "message": "Note logged against ship"}

    return {"action": action, "message": "Unhandled action"}
