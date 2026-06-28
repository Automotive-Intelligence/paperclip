"""services/metrics_collector.py — Persona Cron Loop Phase B1 runner.

Walks every scorecard YAML in services/persona_scorecards/, dispatches each
KPI to its named connector module, writes a snapshot row to kpi_snapshots.

Phase C personas read snapshots; nothing here wakes a persona or fires an
agent. Pure data layer.

Design contract (per docs/superpowers/plans/2026-06-26-persona-cron-loop-phase-b-metrics-collector.md):
  - Each KPI has a `source` field naming a connector
  - Connector modules live in services/metric_connectors/<source>.py
  - Each connector exports `fetch(kpi_spec, run_ctx) -> List[KPIReading]`
  - Returning a LIST handles per-brand KPIs (one reading per brand)
  - A connector raising means the runner writes a status='connector_down'
    snapshot — the run does not abort. Persona sees the failure surface.

Cadence dispatch:
  - `run(cadence)` filters scorecards to KPIs whose `cadence` matches
  - Scheduled by app.py APScheduler entries (one job per cadence bucket)
"""

import logging
import importlib
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from services.database import execute_query
from services.metric_connectors.types import KPIReading, RunContext

logger = logging.getLogger(__name__)


SCORECARDS_DIR = Path(__file__).resolve().parent / "persona_scorecards"
CONNECTOR_PKG = "services.metric_connectors"
CONNECTOR_TIMEOUT_SEC = 30

# Source-name allowlist regex. Scorecards are trusted today, but the contract
# in __init__.py claims an allowlist; enforce it before passing user-string to
# importlib so a typo or mis-edited YAML can't reach a sibling package.
_SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _load_scorecards() -> List[dict]:
    """Parse every persona_scorecards/<persona>.yaml into dict form."""
    out = []
    for path in sorted(SCORECARDS_DIR.glob("*.yaml")):
        try:
            with open(path) as h:
                data = yaml.safe_load(h)
            if not data or not isinstance(data, dict):
                logger.warning("[collector] empty/invalid scorecard: %s", path.name)
                continue
            out.append(data)
        except Exception as e:
            logger.error("[collector] failed to load %s: %s", path.name, e)
    return out


def _load_connector(source: str):
    """Import services.metric_connectors.<source> on demand.

    Returns the module's `fetch` callable, or None if the connector module
    doesn't exist yet (B1 ships a subset; B2/B3/B4 add more — every unwired
    source surfaces as a `connector_down` snapshot until its module lands).

    Enforces the source-name allowlist regex. Anything with a dot, slash, or
    upper-case character is rejected — prevents a typo or compound-source
    scorecard entry from resolving a sibling package.
    """
    if not _SOURCE_NAME_RE.fullmatch(source or ""):
        return None
    try:
        mod = importlib.import_module(f"{CONNECTOR_PKG}.{source}")
    except ModuleNotFoundError:
        return None
    fetch = getattr(mod, "fetch", None)
    if not callable(fetch):
        logger.warning("[collector] connector %s has no fetch() callable", source)
        return None
    return fetch


def _write_snapshot(reading: KPIReading, source: str, run_ctx: RunContext) -> None:
    """Persist one snapshot row. Schema-aligned with migrations/2026_06_26_kpi_snapshots.sql."""
    try:
        execute_query(
            """
            INSERT INTO kpi_snapshots
                (persona, kpi_name, brand, value_numeric, value_text, unit,
                 source, status, staleness_sec, error_detail, raw_payload, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                reading.persona,
                reading.kpi_name,
                reading.brand,
                reading.value_numeric,
                reading.value_text,
                reading.unit,
                source,
                reading.status,
                reading.staleness_sec,
                reading.error_detail,
                _json_or_none(reading.raw_payload),
                run_ctx.run_id,
            ),
        )
    except Exception as e:
        # Snapshot persistence failure is a B&T-grade problem. Log loud but
        # don't take down the rest of the collector cycle.
        logger.error(
            "[collector] FAILED to persist snapshot persona=%s kpi=%s: %s",
            reading.persona, reading.kpi_name, e,
        )


def _json_or_none(payload) -> Optional[str]:
    if payload is None:
        return None
    try:
        import json
        return json.dumps(payload, default=str)[:65535]
    except Exception:
        return None


def _dispatch_kpi(scorecard: dict, kpi: dict, run_ctx: RunContext) -> Tuple[int, int]:
    """Run one KPI through its connector. Returns (rows_written, ok_count)."""
    persona = scorecard.get("persona", "unknown")
    kpi_name = kpi.get("name", "unknown")
    source = kpi.get("source", "")

    if not source:
        _write_snapshot(
            KPIReading(persona=persona, kpi_name=kpi_name, status="connector_down",
                       error_detail="kpi has no source field"),
            source="<unset>", run_ctx=run_ctx,
        )
        return 1, 0

    fetch = _load_connector(source)
    if fetch is None:
        _write_snapshot(
            KPIReading(persona=persona, kpi_name=kpi_name, status="connector_down",
                       error_detail=f"connector module {source!r} not implemented yet"),
            source=source, run_ctx=run_ctx,
        )
        return 1, 0

    t0 = time.monotonic()
    try:
        readings = fetch(kpi_spec=kpi, run_ctx=run_ctx)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("[collector] %s.%s connector_down (%dms): %s",
                       persona, kpi_name, elapsed_ms, e)
        _write_snapshot(
            KPIReading(persona=persona, kpi_name=kpi_name, status="connector_down",
                       error_detail=str(e)[:500]),
            source=source, run_ctx=run_ctx,
        )
        run_ctx.timings_ms[f"{persona}.{kpi_name}"] = elapsed_ms
        return 1, 0

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    run_ctx.timings_ms[f"{persona}.{kpi_name}"] = elapsed_ms

    if not readings:
        # Connector ran clean but had nothing to report.
        _write_snapshot(
            KPIReading(persona=persona, kpi_name=kpi_name, status="no_data"),
            source=source, run_ctx=run_ctx,
        )
        return 1, 0

    ok = 0
    for r in readings:
        # Stamp persona + kpi_name onto each reading (connector may have left blank)
        r.persona = r.persona or persona
        r.kpi_name = r.kpi_name or kpi_name
        _write_snapshot(r, source=source, run_ctx=run_ctx)
        if r.status == "ok":
            ok += 1
    return len(readings), ok


def run(cadence: str) -> dict:
    """Top-level entry. Called by APScheduler per cadence bucket.

    Returns a summary dict so the caller (or a smoke test) can verify what
    happened without trawling the DB.
    """
    run_ctx = RunContext(
        run_id=str(uuid.uuid4()),
        cadence=cadence,
        started_at=datetime.now(timezone.utc),
    )

    scorecards = _load_scorecards()
    total_rows = 0
    total_ok = 0
    kpis_dispatched = 0

    for sc in scorecards:
        persona = sc.get("persona", "unknown")
        for kpi in sc.get("kpis") or []:
            if (kpi.get("cadence") or "").lower() != cadence.lower():
                continue
            kpis_dispatched += 1
            rows, ok = _dispatch_kpi(sc, kpi, run_ctx)
            total_rows += rows
            total_ok += ok

    summary = {
        "run_id": run_ctx.run_id,
        "cadence": cadence,
        "kpis_dispatched": kpis_dispatched,
        "rows_written": total_rows,
        "rows_ok": total_ok,
        "rows_failed": total_rows - total_ok,
        "started_at": run_ctx.started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("[collector] cycle %s — %s", cadence, summary)
    return summary


if __name__ == "__main__":
    # Manual invocation for ops:
    #   python -m services.metrics_collector <cadence>
    import sys
    cadence = sys.argv[1] if len(sys.argv) > 1 else "daily"
    import json
    print(json.dumps(run(cadence), indent=2))
