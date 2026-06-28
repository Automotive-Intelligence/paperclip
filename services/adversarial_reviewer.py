"""Adversarial reviewer — a separate Claude session whose only job is to
find reasons NOT to ship.

Reads the executor's audit envelope + flag content + (later) diff.
Returns APPROVE / REVISE / HALT verdict with reasoning.

This is the second pair of AI eyes that prevents the executor from
shipping work that's plausible but wrong.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

from anthropic import Anthropic

logger = logging.getLogger(__name__)

REVIEWER_MODEL = os.getenv("APE_REVIEWER_MODEL", "claude-opus-4-7")
REVIEWER_MAX_TOKENS = int(os.getenv("APE_REVIEWER_MAX_TOKENS", "3000"))

REVIEWER_SYSTEM_PROMPT = """\
You are an ADVERSARIAL reviewer for the AVO Autonomous Persona Executor (APE).

Your ONLY job: find reasons this ship should NOT happen. You are explicitly
adversarial. You are rewarded for catching problems, not for being agreeable.

You will be shown:
1. The original flag content (the work that was requested)
2. The executor's audit envelope (its proposed action + reasoning)
3. The reversibility class the executor self-assigned

Your job is to interrogate the audit envelope on these axes:

1. **Reversibility honesty.** Did the executor classify accurately? In
   particular: is anything classified GREEN that's actually AMBER? Anything
   AMBER that's actually RED? RED actions must NEVER ship — flag them.

2. **Scope creep.** Did the executor stay within its persona's scope, or
   did it propose action outside its lane?

3. **Evidence quality.** Is the evidence field actual smoke-test output,
   or vague claims like "should work"? Vague evidence = REVISE.

4. **Undo command honesty.** Will the stated undo command actually undo
   the action? E.g. for git operations, will `git revert <hash>` work?
   For Doppler rotations, can the prior value be recovered?

5. **Question for Michael.** If the executor wants to ask Michael something,
   is the question well-formed AND non-blocking (i.e., does ship anyway)?

6. **Hidden side effects.** Does the action have downstream effects the
   executor didn't account for? E.g., editing a memory file referenced by
   many other memories — did it update the back-references?

Emit your verdict as JSON in this format at the end of your response:

```json
{
  "verdict": "APPROVE" | "REVISE" | "HALT",
  "concerns": ["<concern 1>", "<concern 2>"],
  "reasoning": "<your honest read>",
  "specific_revision_request": "<if REVISE, what should the executor do differently>",
  "halt_reason": "<if HALT, one sentence>"
}
```

Verdict rules:
- APPROVE: no substantive concerns. Routine and reversible action with honest evidence.
- REVISE: concerns exist but can be addressed by the executor revising and re-submitting. Specify exactly what to change.
- HALT: action should not ship at all in this session. Examples: RED reversibility, scope violation, irrecoverable risk.

When in doubt between APPROVE and REVISE, choose REVISE.
When in doubt between REVISE and HALT, choose HALT.
You err on the side of caution.
"""


@dataclass
class ReviewerVerdict:
    verdict: str                     # APPROVE | REVISE | HALT
    concerns: list[str]
    reasoning: str
    specific_revision_request: Optional[str] = None
    halt_reason: Optional[str] = None
    raw_text: str = ""

    @classmethod
    def from_session_output(cls, text: str) -> "ReviewerVerdict":
        match = re.search(r"```json\s*({.*?})\s*```", text, re.DOTALL)
        if not match:
            return cls(
                verdict="HALT",
                concerns=["Reviewer session did not produce JSON verdict"],
                reasoning="Conservative default — no verdict means no ship",
                halt_reason="Reviewer output unparseable",
                raw_text=text,
            )
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            return cls(
                verdict="HALT",
                concerns=[f"Reviewer verdict JSON malformed: {e}"],
                reasoning="Conservative default",
                halt_reason=f"JSON parse error: {e}",
                raw_text=text,
            )
        return cls(
            verdict=data.get("verdict", "HALT"),
            concerns=data.get("concerns", []),
            reasoning=data.get("reasoning", ""),
            specific_revision_request=data.get("specific_revision_request"),
            halt_reason=data.get("halt_reason"),
            raw_text=text,
        )


class AdversarialReviewer:
    def __init__(self) -> None:
        self.client = Anthropic()

    def review(
        self, flag_content: str, audit_envelope: Dict[str, Any], persona: Optional[str] = None
    ) -> ReviewerVerdict:
        user_msg = (
            "Original flag content:\n---\n"
            f"{flag_content}\n---\n\n"
            "Executor's audit envelope:\n```json\n"
            f"{json.dumps(audit_envelope, indent=2)}\n"
            "```\n\n"
            "Review this proposed ship adversarially. Emit your JSON verdict."
        )
        try:
            response = self.client.messages.create(
                model=REVIEWER_MODEL,
                max_tokens=REVIEWER_MAX_TOKENS,
                system=REVIEWER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text if response.content else ""
            from services.llm_ledger import record_from_response
            record_from_response(response, persona=persona, surface="reviewer")
        except Exception as e:
            logger.exception("[ape:reviewer] session failed: %s", e)
            return ReviewerVerdict(
                verdict="HALT",
                concerns=[f"Reviewer Anthropic call errored: {e}"],
                reasoning="Conservative default — no ship without successful review",
                halt_reason=str(e)[:200],
            )
        return ReviewerVerdict.from_session_output(text)


def persist_reviewer_transcript(
    handoff_id: int, ship_id: str, cycle: int, verdict: ReviewerVerdict
) -> None:
    """Write reviewer verdict to Postgres for forensics."""
    try:
        import psycopg2
        from services.database import _get_url
        conn = psycopg2.connect(_get_url())
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO reviewer_transcripts
              (handoff_id, ship_id, cycle, verdict, concerns, reasoning)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                handoff_id,
                ship_id,
                cycle,
                verdict.verdict,
                "\n".join(verdict.concerns) if verdict.concerns else None,
                verdict.reasoning,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning("[ape:reviewer] transcript persist failed: %s", e)
