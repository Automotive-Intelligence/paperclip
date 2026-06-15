# services/ape_audit_email.py
"""APE audit email builders — high-impact (immediate) + routine (digest).

Reuses Resend (already wired in morning_briefing.py). Subject + body
shapes match the design spec sections 4.

High-impact ships fire immediately. Routine ships are queued in
ape_routine_digest_queue and flushed by the 6pm CDT cron.
"""

import json
import logging
import os
import requests
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RECIPIENT = os.getenv("PERSONA_EXECUTOR_RECIPIENT") or os.getenv(
    "BRIEFING_RECIPIENT", "michael@automotiveintelligence.io"
)
SENDER = os.getenv(
    "PERSONA_EXECUTOR_FROM",
    "AVO APE <ape@mail.automotiveintelligence.io>",
)
RESEND_URL = "https://api.resend.com/emails"


def _footer_html(ship_id: str, persona: str) -> str:
    return f"""
<hr style="margin-top:32px;border:none;border-top:1px solid #ddd;">
<div style="color:#666;font-size:12px;font-family:-apple-system,Helvetica,Arial,sans-serif;">
<b>HOW TO INTERACT WITH THIS SHIP</b><br>
Reply to this email with one of:<br>
<code>REVERT</code> — undo this ship<br>
<code>PAUSE</code> — disable {persona} autopilot for 24h<br>
<code>PAUSE ALL</code> — disable ALL persona autopilots for 24h<br>
<code>ASK &lt;text&gt;</code> — send question back to the AI<br>
<code>NOTES &lt;text&gt;</code> — log a note against this ship<br>
<br>
<b>WHERE TO ADJUST AUTOPILOT BEHAVIOR</b><br>
Persona prompt: <code>paperclip/services/persona_prompts/{persona.lower()}.md</code><br>
Tool allowlist: <code>paperclip/services/persona_prompts/{persona.lower()}_tools.json</code><br>
Per-persona switch (Railway env): <code>PERSONA_EXECUTOR_{persona.upper()}=on|off</code><br>
Global kill (Railway env): <code>PERSONA_EXECUTOR_ENABLED=off</code><br>
<br>
<b>NOT SURE WHAT TO DO?</b><br>
Reply <code>HELP</code> and the AI will explain the choices in plain English.<br>
<br>
Ship ID: <code>{ship_id}</code>
</div>
"""


def _caution_banner_html(reason: str) -> str:
    return f"""
<div style="background:#fff3cd;border:2px solid #f0ad4e;padding:14px;margin:0 0 18px;border-radius:6px;font-family:-apple-system,Helvetica,Arial,sans-serif;">
<div style="font-size:16px;color:#856404;"><b>⚠️ HEY, YOU SHOULD ACTUALLY LOOK AT THIS, MICHAEL.</b></div>
<div style="font-size:13px;color:#856404;margin-top:6px;">Reason: {reason}</div>
</div>
"""


def _envelope_body_html(env: Dict[str, Any], persona: str, reviewer_note: Optional[str]) -> str:
    risk_color = {"GREEN": "#28a745", "AMBER": "#f0ad4e", "RED": "#dc3545"}.get(
        env.get("reversibility", "GREEN"), "#666"
    )
    caution = (
        _caution_banner_html(env.get("caution_reason") or "(no specific reason given)")
        if env.get("caution_banner_triggered")
        else ""
    )
    question = (
        f"<h3 style='margin-top:22px;color:#856404;'>QUESTION FOR YOU</h3>"
        f"<div style='padding:10px;background:#fffaf2;border-left:3px solid #f0ad4e;'>"
        f"{env.get('question_for_michael')}</div>"
        if env.get("question_for_michael")
        else ""
    )
    reviewer_section = (
        f"<h3 style='margin-top:22px;'>ADVERSARIAL REVIEWER said</h3>"
        f"<div style='font-size:13px;color:#444;'>{reviewer_note}</div>"
        if reviewer_note
        else ""
    )

    return f"""
<!DOCTYPE html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:720px;margin:0 auto;padding:20px;">
{caution}

<h2 style="margin:0 0 8px;">🚨 [{persona}] Auto-shipped</h2>
<div style="color:#666;font-size:14px;margin-bottom:14px;">{env.get('action_summary', '')}</div>

<h3 style="margin-top:22px;">WHAT was done</h3>
<div>{env.get('what_was_done', '')}</div>

<h3 style="margin-top:22px;">WHY</h3>
<div>{env.get('why_done', '')}</div>

<h3 style="margin-top:22px;">EVIDENCE it works</h3>
<pre style="background:#f5f5f5;padding:10px;border-radius:4px;font-size:12px;overflow:auto;">{env.get('evidence', '')}</pre>

<h3 style="margin-top:22px;">RISK profile (AI assessment): <span style="color:{risk_color};">{env.get('reversibility', 'GREEN')}</span></h3>
<div>{env.get('risk_assessment', '')}</div>

<h3 style="margin-top:22px;">UNDO</h3>
<pre style="background:#f5f5f5;padding:10px;border-radius:4px;font-size:12px;">{env.get('undo_command', '(no undo command provided)')}</pre>
<div style="color:#666;font-size:12px;">Or reply <code>REVERT</code> — system will execute it for you.</div>

{reviewer_section}
{question}
{_footer_html(env.get('ship_id', '?'), persona)}
</body></html>
"""


def send_high_impact_ship_email(
    persona: str, envelope: Dict[str, Any], reviewer_note: Optional[str] = None
) -> bool:
    """Fire immediate Resend email for a high-impact ship."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("[ape:email] RESEND_API_KEY not set — skipping high-impact email")
        return False

    subject = f"🚨 [{persona}] Auto-shipped: {envelope.get('action_summary', '')[:60]}"
    html = _envelope_body_html(envelope, persona, reviewer_note)

    try:
        r = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": SENDER,
                "to": [RECIPIENT],
                "subject": subject,
                "html": html,
                "headers": {"X-APE-Ship-ID": envelope.get("ship_id", "")},
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            logger.info(f"[ape:email] high-impact email sent for ship {envelope.get('ship_id')}")
            return True
        logger.error(f"[ape:email] Resend error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"[ape:email] high-impact email send errored: {e}")
        return False


def queue_routine_ship(persona: str, envelope: Dict[str, Any]) -> bool:
    """Append a routine ship to today's digest queue. 6pm cron flushes."""
    try:
        import psycopg2
        from services.database import _get_url
        conn = psycopg2.connect(_get_url())
        cur = conn.cursor()
        # Lazy-create table if missing
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ape_routine_digest_queue (
                id BIGSERIAL PRIMARY KEY,
                persona TEXT NOT NULL,
                ship_id TEXT NOT NULL,
                envelope JSONB NOT NULL,
                queued_at TIMESTAMPTZ DEFAULT NOW(),
                sent_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_routine_digest_unsent ON ape_routine_digest_queue(persona, queued_at) WHERE sent_at IS NULL;
            """
        )
        cur.execute(
            "INSERT INTO ape_routine_digest_queue (persona, ship_id, envelope) VALUES (%s, %s, %s)",
            (persona, envelope.get("ship_id", ""), json.dumps(envelope)),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"[ape:email] routine queue insert failed: {e}")
        return False


def send_daily_digest(persona: str) -> bool:
    """Flush today's queued routine ships into one digest email per persona."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        import psycopg2
        from services.database import _get_url, fetch_all
        rows = fetch_all(
            """
            SELECT id, ship_id, envelope, queued_at
            FROM ape_routine_digest_queue
            WHERE persona = %s AND sent_at IS NULL
            ORDER BY queued_at ASC
            """,
            (persona,),
        )
    except Exception as e:
        logger.warning(f"[ape:email] digest pull failed: {e}")
        return False

    if not rows:
        logger.info(f"[ape:email] no routine ships for {persona} today — skipping digest")
        return False

    rows_html = "".join(
        f"<tr><td style='padding:6px 10px;'>{(json.loads(env) if isinstance(env, str) else env).get('action_summary', '?')}</td>"
        f"<td style='padding:6px 10px;color:#666;font-size:11px;'>{ship_id[:8]}</td></tr>"
        for (_id, ship_id, env, _qa) in rows
    )
    html = f"""
<!DOCTYPE html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:720px;margin:0 auto;padding:20px;">
<h2>🛠 [{persona}] Today's autopilot — {len(rows)} routine ship(s)</h2>
<table style="width:100%;border-collapse:collapse;font-size:13px;">{rows_html}</table>
{_footer_html("digest", persona)}
</body></html>
"""

    try:
        r = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": SENDER,
                "to": [RECIPIENT],
                "subject": f"🛠 [{persona}] Today's autopilot — {len(rows)} routine ship(s)",
                "html": html,
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            # Mark all as sent
            ids = tuple(r[0] for r in rows)
            import psycopg2
            from services.database import _get_url
            conn = psycopg2.connect(_get_url())
            cur = conn.cursor()
            cur.execute(
                f"UPDATE ape_routine_digest_queue SET sent_at = NOW() WHERE id IN %s",
                (ids,),
            )
            conn.commit()
            cur.close()
            conn.close()
            return True
        logger.error(f"[ape:email] digest send failed {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"[ape:email] digest send errored: {e}")
        return False
