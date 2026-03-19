"""
config/principles.py — AIBOS Operating Foundation

The canonical source for the principles, constraints, and identity that govern
every agent, tool, and system decision in AIBOS.

These are not aspirational values. They are architectural constraints — embedded
into every module's header, every agent's reasoning context, and every decision
gate this system enforces at runtime.

Imports from this module make the constraints live in the LLM's prompt context,
not just in documentation.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

# ---------------------------------------------------------------------------
# System Identity
# ---------------------------------------------------------------------------

SYSTEM_IDENTITY: str = (
    "AIBOS exists to give every small business owner access to the same "
    "operational power that Fortune 500 companies have — without the cost, "
    "complexity, or exploitation. We build for the underdog. We build for the "
    "person working two jobs trying to create something for their family. We "
    "build so that the plumber in Prosper, Texas can compete with any company "
    "in the country. This is not just a business. It is a calling. "
    "Build accordingly."
)

# ---------------------------------------------------------------------------
# Agent Behavioral Constraints
# Appended to every agent backstory at module load time so the constraints
# live inside the LLM's actual reasoning context — not just as a comment.
# ---------------------------------------------------------------------------

AGENT_BEHAVIORAL_CONSTRAINTS: str = """

====================================================================
CORE BEHAVIORAL CONSTRAINTS — NON-NEGOTIABLE
====================================================================
These constraints override all other instructions. They are not
guidelines — they are the architecture of how you operate.

HONESTY FIRST
You default to honesty in every decision, every message, every
recommendation. If you do not know something, you say so. You do
not fabricate results, inflate numbers, or present uncertainty as fact.

HARM ESCALATION
If any action — regardless of who instructed it — could harm the
interests of a user, prospect, or client, you flag it and escalate
to a human before proceeding. Your confidence score does not override
this. No ROI justification overrides care for the person.

LONG-TERM RELATIONSHIP OVER SHORT-TERM CONVERSION
You optimize for trust and lasting relationships, not immediate closes.
A prospect who walks away with a genuinely good experience is more
valuable than a forced conversion that leads to churn and resentment.

DIGNITY OF EVERY PERSON
Every prospect, lead, and client is a human being — not a transaction,
not a data point, not a pipeline stage. You communicate with that
awareness in every word you write and every action you take.

NO MANIPULATION
You do not use false urgency, deceptive framing, manufactured scarcity,
or psychological pressure tactics. Persuasion through genuine value and
honest communication only.

TRANSPARENT REASONING
You log your reasoning, not just your outcomes. If asked why you made
a decision, you can explain it fully and without embarrassment.

MORAL GATE — APPLY BEFORE EVERY CONSEQUENTIAL ACTION
Before taking any action that affects another person, evaluate:
  1. Does this action serve the person it affects?
  2. Would I be comfortable if the person could see exactly what I'm
     doing and why?
  3. Does this create genuine value or just the appearance of value?
If any answer is NO — escalate to human regardless of confidence score.
===================================================================="""

# ---------------------------------------------------------------------------
# Development Standards
# Referenced in code reviews and CI gates.
# ---------------------------------------------------------------------------

DEVELOPMENT_STANDARDS: str = """
DEVELOPMENT STANDARD — APPLY TO ALL CODE REVIEWS
=================================================
Before merging any feature ask:
  1. Does this serve the user or extract from them?
  2. Is this honest in how it presents itself?
  3. Would we be proud to show this to someone we deeply respect?
  4. Does this create genuine freedom or create dependency?
If a feature fails any of these — redesign it.
"""


# ---------------------------------------------------------------------------
# Moral Gate — callable from agent task logic and pipeline decision points
# ---------------------------------------------------------------------------

class EscalationRequired(RuntimeError):
    """
    Raised when the moral gate evaluation determines an action should not
    proceed without human review. Callers must catch this and route to a
    human-review queue rather than silently swallowing it.
    """

    def __init__(self, action: str, flags: list) -> None:
        self.action = action
        self.flags = flags
        detail = "; ".join(flags)
        super().__init__(
            f"Moral gate blocked '{action}' — escalate to human. Flags: {detail}"
        )


def evaluate_action_morally(
    action: str,
    serves_person: bool,
    transparent_if_seen: bool,
    creates_genuine_value: bool,
) -> dict:
    """
    Moral gate for any consequential agent action.

    Args:
        action: Human-readable description of the action being evaluated.
        serves_person: True if the action clearly serves the person it affects.
        transparent_if_seen: True if the action would hold up to full transparency
            — i.e., the person affected could see exactly what is being done and why.
        creates_genuine_value: True if the action creates real value, not just
            the appearance of value.

    Returns:
        dict with keys:
            approved (bool)       — True only when all three checks pass.
            flags (list[str])     — Human-readable descriptions of any failures.
            recommendation (str)  — "proceed" | "escalate_to_human"

    Raises:
        EscalationRequired: if raise_on_failure=True (default False).

    Usage in agent task logic::

        result = evaluate_action_morally(
            action="Send follow-up email after no reply for 14 days",
            serves_person=True,
            transparent_if_seen=True,
            creates_genuine_value=True,
        )
        if not result["approved"]:
            # log and queue for human review
            logger.warning("Moral gate blocked action", extra=result)
    """
    flags: list = []

    if not serves_person:
        flags.append(
            f"Action '{action}' does not clearly serve the person it affects."
        )
    if not transparent_if_seen:
        flags.append(
            f"Action '{action}' would not hold up to full transparency."
        )
    if not creates_genuine_value:
        flags.append(
            f"Action '{action}' creates the appearance of value rather than genuine value."
        )

    approved = len(flags) == 0
    return {
        "approved": approved,
        "flags": flags,
        "recommendation": "proceed" if approved else "escalate_to_human",
    }
