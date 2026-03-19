"""
services/delivery_receipt.py — Delivery Telemetry for AIBOS Artifacts

Every artifact that leaves the system generates a DeliveryReceipt.
A receipt captures what channel was used, what happened (delivered / failed),
and any error detail. Receipts are the feedback loop: "who saw this, where
was it sent, and what happened next?"

All receipts are persisted to Postgres so every dispatch action is auditable.
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

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DeliveryReceipt dataclass
# ---------------------------------------------------------------------------

RECEIPT_STATUSES = frozenset({
    "dispatched",   # sent to adapter; awaiting confirmation
    "delivered",    # adapter confirmed delivery
    "failed",       # adapter reported failure (non-retryable)
    "bounced",      # email/sms hard bounce
    "opened",       # email/message opened (tracked via webhook)
    "clicked",      # link clicked
    "replied",      # recipient replied
    "booked",       # recipient booked a meeting (highest-value event)
})


@dataclass
class DeliveryReceipt:
    receipt_id: str
    artifact_id: str
    channel: str                           # "email", "crm", "sms", "linkedin", …
    status: str                            # one of RECEIPT_STATUSES
    delivered_at: Optional[datetime.datetime]
    error: Optional[str]
    provider_response: Optional[Dict[str, Any]]  # raw adapter response for debugging
    created_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "artifact_id": self.artifact_id,
            "channel": self.channel,
            "status": self.status,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "error": self.error,
            "provider_response": self.provider_response or {},
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_receipt(
    artifact_id: str,
    channel: str,
    *,
    status: str = "dispatched",
    error: Optional[str] = None,
    provider_response: Optional[Dict[str, Any]] = None,
) -> DeliveryReceipt:
    """Build a DeliveryReceipt without persisting it."""
    import uuid
    delivered_at = datetime.datetime.utcnow() if status in ("delivered", "dispatched") else None
    return DeliveryReceipt(
        receipt_id=str(uuid.uuid4()),
        artifact_id=artifact_id,
        channel=channel,
        status=status,
        delivered_at=delivered_at,
        error=error,
        provider_response=provider_response,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def record_receipt(receipt: DeliveryReceipt) -> None:
    """Persist a DeliveryReceipt to the delivery_receipts table.

    Fails silently with a log entry rather than crashing the dispatch path.
    The receipt object still carries the outcome for the caller.
    """
    try:
        execute_query(
            """
            INSERT INTO delivery_receipts
                (receipt_id, artifact_id, channel, status, delivered_at, error,
                 provider_response, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (receipt_id) DO NOTHING
            """,
            (
                receipt.receipt_id,
                receipt.artifact_id,
                receipt.channel,
                receipt.status,
                receipt.delivered_at,
                receipt.error,
                json.dumps(receipt.provider_response or {}),
                receipt.created_at,
            ),
        )
        logger.info(
            "[receipt] ✓ artifact=%s channel=%s status=%s",
            receipt.artifact_id, receipt.channel, receipt.status,
        )
    except DatabaseError as exc:
        logger.error("[receipt] ✗ persist failed: %s", exc)


def get_receipts(artifact_id: str) -> List[DeliveryReceipt]:
    """Return all delivery receipts for a given artifact, newest first."""
    try:
        rows = fetch_all(
            "SELECT receipt_id, artifact_id, channel, status, delivered_at, "
            "       error, provider_response, created_at "
            "FROM delivery_receipts "
            "WHERE artifact_id = %s "
            "ORDER BY created_at DESC",
            (artifact_id,),
        )
    except DatabaseError as exc:
        logger.error("[receipt] get_receipts query failed: %s", exc)
        return []

    results: List[DeliveryReceipt] = []
    for row in rows:
        try:
            pr = json.loads(row[6]) if row[6] else {}
        except (json.JSONDecodeError, TypeError):
            pr = {}
        results.append(DeliveryReceipt(
            receipt_id=row[0],
            artifact_id=row[1],
            channel=row[2],
            status=row[3],
            delivered_at=row[4],
            error=row[5],
            provider_response=pr,
            created_at=row[7],
        ))
    return results
