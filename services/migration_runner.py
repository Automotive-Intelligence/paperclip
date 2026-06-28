"""services/migration_runner.py — auto-apply unseen migrations at startup.

Walks migrations/*.sql in lexical order, applies any file that hasn't been
recorded in the `schema_migrations` ledger, and records the apply on success.

WHY THIS EXISTS
---------------
Paperclip had no in-process migration runner — every new migration required
a manual `railway run psql` against the production DB, which is gated and
easy to forget. The 2026-06-26 kpi_snapshots migration was the immediate
trigger: PR B1a needed the schema to land before the first collector cycle,
and there was no automated path to apply it.

DESIGN
------
- One ledger table `schema_migrations(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ)`
- Runner scans migrations/ for *.sql, sorts by filename (the existing date-prefix
  convention sorts chronologically — see migrations/2026_06_14_ape_tables.sql)
- For each file: SELECT 1 FROM schema_migrations WHERE filename = ? — skip if seen
- Otherwise execute the file (single execute_query for the whole text — psycopg
  handles multi-statement strings), then insert the row
- Failures bubble up with the filename so the operator knows which one broke;
  callers can decide whether to fail-fast or continue
- Idempotent: re-running across deploys is safe

NOT TRYING TO BE
----------------
- Rollback / down migrations (we never rollback prod; we ship a fix migration)
- Multi-tenant or per-schema migrations
- Transactional wrap-everything (some DDL like CREATE INDEX CONCURRENTLY can't
  run inside a tx; let each file manage its own atomicity)

USAGE
-----
At app startup (lifespan):

    from services.migration_runner import apply_pending
    summary = apply_pending()
    logging.info(f"[Migrations] {summary}")

Manually for one-shot:

    python -m services.migration_runner
"""

import logging
import os
from pathlib import Path
from typing import Dict, List

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

# Migrations live at repo-root/migrations/. This module lives at services/, so
# resolve via __file__ rather than CWD (CWD differs between dev and Railway).
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _ensure_ledger() -> None:
    """Idempotent — creates schema_migrations if missing."""
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def _applied_set() -> set:
    """Return the set of filenames already recorded as applied."""
    rows = fetch_all("SELECT filename FROM schema_migrations")
    # fetch_all returns List[Tuple]; filename is column 0
    return {row[0] for row in rows}


def _list_pending() -> List[Path]:
    """Lexically-sorted list of *.sql files not yet applied."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    applied = _applied_set()
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())
    return [p for p in files if p.name not in applied]


def apply_pending() -> Dict:
    """Apply every migration not yet recorded. Returns summary dict.

    Returns: {"applied": [...], "skipped_already_applied": N, "errors": [{file, error}, ...]}
    """
    if not (os.getenv("DATABASE_URL") or "").strip():
        return {"applied": [], "skipped_already_applied": 0, "errors": [],
                "note": "DATABASE_URL not configured — migrations skipped"}

    _ensure_ledger()
    applied_now: List[str] = []
    errors: List[Dict] = []

    pending = _list_pending()
    if not pending:
        total_applied = len(_applied_set())
        return {"applied": [], "skipped_already_applied": total_applied, "errors": []}

    for path in pending:
        try:
            sql = path.read_text()
            if not sql.strip():
                continue
            execute_query(sql)
            execute_query(
                "INSERT INTO schema_migrations (filename) VALUES (%s) "
                "ON CONFLICT (filename) DO NOTHING",
                (path.name,),
            )
            applied_now.append(path.name)
            logger.info("[migrations] applied %s", path.name)
        except Exception as e:
            errors.append({"file": path.name, "error": str(e)[:500]})
            logger.error("[migrations] FAILED applying %s: %s", path.name, e)
            # Stop on first failure — don't apply later migrations on top of a
            # broken state. Operator fixes the migration, redeploys.
            break

    return {
        "applied": applied_now,
        "skipped_already_applied": len(_applied_set()) - len(applied_now),
        "errors": errors,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(apply_pending(), indent=2, default=str))
