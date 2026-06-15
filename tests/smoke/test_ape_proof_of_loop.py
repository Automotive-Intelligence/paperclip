"""End-to-end smoke for APE Phase 1.

Runs via: railway run --service paperclip python3 -m tests.smoke.test_ape_proof_of_loop

Steps:
  1. Insert a trivial Infrastructure-targeted flag into agent_handoffs
  2. Set PERSONA_EXECUTOR_INFRASTRUCTURE=on (must be set on Railway env beforehand)
  3. Call ape_tick() directly
  4. Verify the flag's ape_status is 'shipped' or 'halted'
  5. Verify reviewer_transcripts has at least one row for the ship
  6. Print outcome

SCHEMA NOTE — IMPORTANT:
  The plan in Task 6.1 references columns `source_file`, `target`, `flag_content`,
  `posted_at` on `agent_handoffs`. Those columns DO NOT exist. Verified actual
  columns are: id, from_agent, to_agent, river, handoff_type, payload, priority,
  status, created_at, picked_up_at, completed_at, plus the 8 ape_* columns.

  This test inserts using the real schema. But note: services.persona_executor
  .pull_pending_flags() ALSO queries the non-existent columns and will fail
  with a SQL error. That bug is captured by this smoke and must be fixed
  before APE can ship a real flag. See report.
"""

import json
import uuid
from datetime import datetime, timezone


def main() -> int:
    from services.database import fetch_all, _get_url
    import psycopg2

    test_flag_id = str(uuid.uuid4())[:8]
    test_flag_content = (
        f"FLAG FOR: Infrastructure\n"
        f"**What:** Proof-of-loop test {test_flag_id} — emit an audit envelope with "
        f"action_summary='APE proof-of-loop {test_flag_id}', reversibility='GREEN', "
        f"impact_tier='ROUTINE'. Do NOT actually edit any files. Return halt_requested=false "
        f"with a fabricated 'evidence' field of 'proof-of-loop dry run'.\n"
        f"**Why now:** Validates end-to-end without touching state.\n"
        f"**Posted by:** Infrastructure\n"
        f"**Posted:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
    )

    # Insert test flag — uses the REAL schema (from_agent/to_agent/payload),
    # not the plan's (source_file/target/flag_content).
    conn = psycopg2.connect(_get_url())
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_handoffs (
            from_agent, to_agent, river, handoff_type, payload,
            priority, status, created_at, ape_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), 'queued')
        RETURNING id
        """,
        (
            "infrastructure",       # from_agent
            "Infrastructure",       # to_agent (where APE polls)
            "infrastructure",       # river
            "test_flag",            # handoff_type
            test_flag_content,      # payload (carries the flag body)
            "normal",               # priority
            "pending",              # status
        ),
    )
    handoff_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    print(f"inserted test handoff id={handoff_id}")

    # Run executor tick (caller is responsible for setting env var)
    from services.persona_executor import ape_tick
    result = ape_tick()
    print(f"tick result: {result}")

    # Verify outcome
    rows = fetch_all(
        "SELECT ape_status, ape_audit_envelope FROM agent_handoffs WHERE id = %s",
        (handoff_id,),
    )
    if not rows:
        print("FAIL: no handoff row found")
        return 1
    status, envelope = rows[0]
    print(f"final status: {status}")
    print(f"envelope: {json.dumps(envelope, indent=2) if envelope else None}")

    reviewer_rows = fetch_all(
        "SELECT cycle, verdict FROM reviewer_transcripts WHERE handoff_id = %s ORDER BY cycle",
        (handoff_id,),
    )
    print(f"reviewer transcripts: {reviewer_rows}")

    if status not in ("shipped", "halted"):
        print(f"FAIL: unexpected status {status}")
        return 2
    if not reviewer_rows:
        print("FAIL: no reviewer transcript")
        return 3

    print("OK: proof-of-loop completed end-to-end")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
