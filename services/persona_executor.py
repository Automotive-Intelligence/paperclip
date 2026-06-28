# services/persona_executor.py
"""Autonomous Persona Executor — picks up flags from agent_handoffs,
spawns ephemeral Claude sessions to execute, ships via existing
GitHub + Postgres surfaces.

Phase 1: Infrastructure persona only. Feature-flagged per persona via
PERSONA_EXECUTOR_<PERSONA>=on env var. Reviewer + email + reply parser
land in subsequent PRs.
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from anthropic import Anthropic

from services.database import fetch_all
from services.persona_prompts import load_persona_prompt, load_persona_tools

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = os.getenv("APE_MODEL", "claude-opus-4-7")
SESSION_TIMEOUT_SECONDS = int(os.getenv("APE_SESSION_TIMEOUT_S", "600"))
MAX_TOKENS = int(os.getenv("APE_MAX_TOKENS", "8000"))


@dataclass
class AuditEnvelope:
    ship_id: str
    flag_id: str
    action_summary: str
    what_was_done: str = ""
    why_done: str = ""
    evidence: str = ""
    impact_tier: str = "ROUTINE"             # ROUTINE | HIGH-IMPACT
    reversibility: str = "GREEN"             # GREEN | AMBER | RED
    undo_command: str = ""
    risk_assessment: str = ""
    caution_banner_triggered: bool = False
    caution_reason: Optional[str] = None
    question_for_michael: Optional[str] = None
    halt_requested: bool = False
    halt_reason: Optional[str] = None

    @classmethod
    def from_session_output(cls, text: str, flag_id: str) -> "AuditEnvelope":
        """Parse JSON envelope from a session's final message text."""
        # Sessions are instructed to emit JSON at the end. We grep for the
        # last fenced JSON block.
        import re
        match = re.search(r"```json\s*({.*?})\s*```", text, re.DOTALL)
        if not match:
            # No envelope — treat as halt
            return cls(
                ship_id=str(uuid.uuid4()),
                flag_id=flag_id,
                action_summary="Session ended without producing audit envelope",
                halt_requested=True,
                halt_reason="No JSON audit envelope found in session output",
            )
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            return cls(
                ship_id=str(uuid.uuid4()),
                flag_id=flag_id,
                action_summary="Audit envelope JSON malformed",
                halt_requested=True,
                halt_reason=f"JSON parse error: {e}",
            )
        # Coerce into dataclass with defaults
        return cls(
            ship_id=data.get("ship_id") or str(uuid.uuid4()),
            flag_id=flag_id,
            action_summary=data.get("action_summary", ""),
            what_was_done=data.get("what_was_done", ""),
            why_done=data.get("why_done", ""),
            evidence=data.get("evidence", ""),
            impact_tier=data.get("impact_tier", "ROUTINE"),
            reversibility=data.get("reversibility", "GREEN"),
            undo_command=data.get("undo_command", ""),
            risk_assessment=data.get("risk_assessment", ""),
            caution_banner_triggered=bool(data.get("caution_banner_triggered")),
            caution_reason=data.get("caution_reason"),
            question_for_michael=data.get("question_for_michael"),
            halt_requested=bool(data.get("halt_requested")) or data.get("reversibility") == "RED",
            halt_reason=data.get("halt_reason"),
        )


class PersonaExecutor:
    """Polls agent_handoffs for unassigned flags + spawns executor sessions.

    Phase 1: only handles persona='Infrastructure' rows. Feature-flagged.
    """

    def __init__(self) -> None:
        self.client = Anthropic()

    def is_persona_enabled(self, persona: str) -> bool:
        env_name = f"PERSONA_EXECUTOR_{persona.upper()}"
        return os.getenv(env_name, "off").lower() == "on"

    def is_persona_paused(self, persona: str) -> bool:
        try:
            rows = fetch_all(
                "SELECT 1 FROM persona_executor_pause WHERE persona IN (%s, %s) "
                "AND paused_until > NOW() LIMIT 1",
                (persona.upper(), "*"),
            )
            return len(rows) > 0
        except Exception as e:
            logger.warning(f"[ape] pause check failed: {e}")
            return False

    def pull_pending_flags(self) -> list[Dict[str, Any]]:
        """Return rows from agent_handoffs that need APE execution."""
        try:
            rows = fetch_all(
                """
                SELECT id, handoff_type AS source_file, to_agent AS target, payload AS flag_content, created_at AS posted_at
                FROM agent_handoffs
                WHERE to_agent = 'Infrastructure'
                  AND (ape_status IS NULL OR ape_status = 'queued')
                ORDER BY created_at ASC
                LIMIT 5
                """
            )
        except Exception as e:
            logger.warning(f"[ape] pull_pending_flags failed: {e}")
            return []
        return [
            {
                "id": r[0],
                "source_file": r[1],
                "target": r[2],
                "flag_content": r[3],
                "posted_at": r[4],
            }
            for r in rows
        ]

    def execute_flag(self, flag: Dict[str, Any]) -> AuditEnvelope:
        """Spawn an ephemeral Claude session to execute one flag."""
        persona = flag["target"]
        system_prompt = load_persona_prompt(persona)
        tool_allowlist = load_persona_tools(persona)

        from services.current_time import current_time_block
        user_message = (
            f"{current_time_block()}\n\n"
            f"A flag has been posted to {persona}. Read the flag, plan, "
            f"execute per your scope, verify, and emit the JSON audit "
            f"envelope.\n\n"
            f"Flag ID: {flag['id']}\n"
            f"Source file: {flag['source_file']}\n"
            f"Posted: {flag['posted_at']}\n\n"
            f"--- FLAG CONTENT ---\n{flag['flag_content']}\n--- END FLAG ---"
        )

        # NOTE: Phase 1 session is single-turn — we send the message,
        # collect the model's response (which should contain the
        # JSON envelope at the end), parse it. v2 will add multi-turn
        # tool-use loop for real action execution.
        try:
            response = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text if response.content else ""
            from services.llm_ledger import record_from_response
            record_from_response(response, persona=persona, surface="executor")
        except Exception as e:
            logger.exception("[ape] session failed for flag %s: %s", flag["id"], e)
            return AuditEnvelope(
                ship_id=str(uuid.uuid4()),
                flag_id=str(flag["id"]),
                action_summary="Session errored before producing envelope",
                halt_requested=True,
                halt_reason=str(e)[:200],
            )

        return AuditEnvelope.from_session_output(text, str(flag["id"]))

    def review_and_revise(
        self,
        flag: Dict[str, Any],
        initial_envelope: AuditEnvelope,
        max_cycles: int = 3,
    ) -> tuple[AuditEnvelope, list]:
        """Run adversarial review loop. Returns (final_envelope, verdicts_list).

        Cycles up to max_cycles. On REVISE, re-prompts executor with reviewer's
        specific_revision_request. On HALT or APPROVE, returns immediately.
        """
        from services.adversarial_reviewer import AdversarialReviewer, persist_reviewer_transcript

        reviewer = AdversarialReviewer()
        verdicts = []
        envelope = initial_envelope

        for cycle in range(1, max_cycles + 1):
            verdict = reviewer.review(flag["flag_content"], asdict(envelope), persona=flag["target"])
            verdicts.append(verdict)
            persist_reviewer_transcript(flag["id"], envelope.ship_id, cycle, verdict)

            if verdict.verdict == "APPROVE":
                return envelope, verdicts
            if verdict.verdict == "HALT":
                envelope.halt_requested = True
                envelope.halt_reason = (
                    f"Reviewer HALT (cycle {cycle}): {verdict.halt_reason or verdict.reasoning[:200]}"
                )
                return envelope, verdicts
            # REVISE — re-execute with reviewer concerns prepended
            if cycle < max_cycles:
                envelope = self._reexecute_with_revisions(flag, envelope, verdict)

        # Ran out of cycles without APPROVE — halt
        envelope.halt_requested = True
        envelope.halt_reason = (
            f"Reviewer requested revisions {max_cycles} times without APPROVE"
        )
        return envelope, verdicts

    def _reexecute_with_revisions(
        self, flag: Dict[str, Any], prior_envelope: AuditEnvelope, verdict
    ) -> AuditEnvelope:
        """Re-spawn executor session with reviewer's revision request."""
        system_prompt = load_persona_prompt(flag["target"])
        from services.current_time import current_time_block
        user_msg = (
            f"{current_time_block()}\n\n"
            f"You proposed a ship that the adversarial reviewer asked you to revise.\n\n"
            f"--- ORIGINAL FLAG ---\n{flag['flag_content']}\n--- END FLAG ---\n\n"
            f"--- YOUR PRIOR ENVELOPE ---\n{json.dumps(asdict(prior_envelope), indent=2)}\n"
            f"--- END ENVELOPE ---\n\n"
            f"--- REVIEWER CONCERNS ---\n"
            + "\n".join(f"- {c}" for c in verdict.concerns)
            + f"\n\nReviewer specifically requests: {verdict.specific_revision_request}\n"
            f"--- END CONCERNS ---\n\n"
            f"Re-execute with the reviewer's concerns addressed. Emit a fresh JSON audit envelope."
        )
        try:
            response = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text if response.content else ""
            from services.llm_ledger import record_from_response
            record_from_response(response, persona=flag["target"], surface="executor:revision")
        except Exception as e:
            logger.exception("[ape] revision session failed: %s", e)
            return AuditEnvelope(
                ship_id=prior_envelope.ship_id,
                flag_id=prior_envelope.flag_id,
                action_summary="Revision session errored",
                halt_requested=True,
                halt_reason=str(e)[:200],
            )
        return AuditEnvelope.from_session_output(text, prior_envelope.flag_id)

    def record_outcome(self, flag_id: int, envelope: AuditEnvelope) -> None:
        """Persist envelope + outcome to agent_handoffs row."""
        try:
            import psycopg2
            from services.database import _get_url
            now = datetime.now(timezone.utc)
            conn = psycopg2.connect(_get_url())
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE agent_handoffs
                SET ape_session_id = %s,
                    ape_status = %s,
                    ape_impact_tier = %s,
                    ape_reversibility = %s,
                    ape_undo_command = %s,
                    ape_audit_envelope = %s,
                    ape_completed_at = %s
                WHERE id = %s
                """,
                (
                    envelope.ship_id,
                    "halted" if envelope.halt_requested else "shipped",
                    envelope.impact_tier,
                    envelope.reversibility,
                    envelope.undo_command,
                    json.dumps(asdict(envelope)),
                    now,
                    flag_id,
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"[ape] record_outcome failed: {e}")

        # Capture pre-ship metrics for 24h regression correlation
        if not envelope.halt_requested:
            try:
                from services.ape_ship_telemetry import record_pre_snapshot
                record_pre_snapshot(flag_id, envelope.ship_id, "Infrastructure")
            except Exception as e:
                logger.warning(f"[ape] pre-snapshot record failed: {e}")

    def _dispatch_notifications(
        self, persona: str, envelope: AuditEnvelope, reviewer_note: Optional[str] = None
    ) -> None:
        """Fire email per impact tier — high-impact immediate, routine to digest queue."""
        if envelope.halt_requested:
            # Halted ships do not produce ship emails. They post a "couldn't execute"
            # flag back to Infrastructure (handled separately).
            return
        from services.ape_audit_email import send_high_impact_ship_email, queue_routine_ship
        env_dict = asdict(envelope)
        if envelope.impact_tier == "HIGH-IMPACT":
            send_high_impact_ship_email(persona, env_dict, reviewer_note)
        else:
            queue_routine_ship(persona, env_dict)

    def tick(self) -> Dict[str, int]:
        """APScheduler entry point. Process up to 5 pending flags."""
        processed = 0
        halted = 0
        shipped = 0
        for flag in self.pull_pending_flags():
            persona = flag["target"]
            if not self.is_persona_enabled(persona):
                logger.debug(f"[ape] persona {persona} disabled — skipping")
                continue
            if self.is_persona_paused(persona):
                logger.info(f"[ape] persona {persona} paused — skipping")
                continue
            envelope = self.execute_flag(flag)
            envelope, verdicts = self.review_and_revise(flag, envelope)
            self.record_outcome(flag["id"], envelope)
            reviewer_note = verdicts[-1].verdict if verdicts else None
            self._dispatch_notifications(flag["target"], envelope, reviewer_note)
            processed += 1
            if envelope.halt_requested:
                halted += 1
            else:
                shipped += 1
        return {"processed": processed, "shipped": shipped, "halted": halted}


def ape_tick() -> Dict[str, int]:
    """Module-level entry point — APScheduler + /admin/run-now both call this."""
    return PersonaExecutor().tick()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(ape_tick())
