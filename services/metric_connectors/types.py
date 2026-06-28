"""Shared dataclasses for the metrics collector + its connectors.

Lives separately from services.metrics_collector to avoid circular imports —
connector modules import KPIReading/RunContext from here, and the collector
runner also imports from here. Both sides depend on this leaf module, not on
each other.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class KPIReading:
    """One snapshot row, returned by a connector's fetch().

    `brand` is None for org-level KPIs (e.g. token_budget_burn_rate). It carries
    a brand slug ("wd", "avi", "aipg", "bookd", "pp", "agent_empire") when the
    KPI is per_brand.
    """
    persona: str
    kpi_name: str
    brand: Optional[str] = None
    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    status: str = "ok"
    staleness_sec: Optional[int] = None
    error_detail: Optional[str] = None
    raw_payload: Optional[dict] = None


@dataclass
class RunContext:
    """Per-cycle context handed to every connector. Shared run_id ties
    snapshots from one collector cycle together so a brief can report
    "12 / 30 connectors green this morning, 3 stale, 1 down."
    """
    run_id: str
    cadence: str
    started_at: datetime
    timings_ms: Dict[str, int] = field(default_factory=dict)
