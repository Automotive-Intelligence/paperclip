"""doppler_audit connector — B&T secrets_rotation_compliance KPI.

% of secrets in the canonical Doppler project that were last set within the
90-day rotation window. Calls Doppler's REST API directly (no shell-out to
the doppler CLI — that depends on local auth + isn't available inside
production containers).

Auth: DOPPLER_AUDIT_TOKEN — service token scoped to read-only on the project.
If unset, surfaces as connector_down (graceful — sweep doesn't fall over).

The doppler API uses a project + config (env) tuple, e.g. (paperclip, prd).
Falls back to env vars DOPPLER_AUDIT_PROJECT / DOPPLER_AUDIT_CONFIG.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List

import requests

from services.metric_connectors.types import KPIReading

logger = logging.getLogger(__name__)

_API_BASE = "https://api.doppler.com/v3"
_REQUEST_TIMEOUT = 12
_ROTATION_WINDOW_DAYS = 90

# Secrets that don't need 90-day rotation (system-injected, oauth-managed,
# or vendor-rotated-internally). Excluded from compliance denominator.
_EXEMPT_PREFIXES = (
    "DOPPLER_",       # Doppler's own injected vars
    "RAILWAY_",       # Railway's injected vars
    "STRIPE_PUBLISHABLE_",  # public keys, no rotation needed
    "TWENTY_AVI_API_URL",   # workspace URL, not a secret
    "TWENTY_WD_URL",
    "TWENTY_BOOKD_URL",
)


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name != "secrets_rotation_compliance":
        raise ValueError(f"doppler_audit: unsupported kpi {name!r}")
    return [_rotation_compliance()]


def _rotation_compliance() -> KPIReading:
    token = (os.getenv("DOPPLER_AUDIT_TOKEN") or "").strip()
    if not token:
        return KPIReading(
            persona="bt",
            kpi_name="secrets_rotation_compliance",
            status="connector_down",
            error_detail="DOPPLER_AUDIT_TOKEN not set",
        )

    project = (os.getenv("DOPPLER_AUDIT_PROJECT") or "paperclip").strip()
    config = (os.getenv("DOPPLER_AUDIT_CONFIG") or "prd").strip()

    try:
        r = requests.get(
            f"{_API_BASE}/configs/config/secrets/names",
            auth=(token, ""),
            params={"project": project, "config": config, "include_dynamic_secrets": "false"},
            timeout=_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        names = (r.json() or {}).get("names") or []
    except Exception as e:
        return KPIReading(
            persona="bt",
            kpi_name="secrets_rotation_compliance",
            status="connector_down",
            error_detail=f"name list failed: {e}",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=_ROTATION_WINDOW_DAYS)
    compliant = 0
    counted = 0
    stale: list = []

    for name in names:
        # Skip exempt prefixes (system-injected vars)
        if any(name.startswith(p) for p in _EXEMPT_PREFIXES):
            continue
        try:
            r = requests.get(
                f"{_API_BASE}/configs/config/secret",
                auth=(token, ""),
                params={"project": project, "config": config, "name": name},
                timeout=_REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            sec = (r.json() or {}).get("value") or {}
            modified_at = sec.get("modifiedAt") or sec.get("modified_at") or ""
            if modified_at:
                try:
                    dt = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    counted += 1
                    if dt >= cutoff:
                        compliant += 1
                    else:
                        days_old = (datetime.now(timezone.utc) - dt).days
                        stale.append({"name": name, "days_since_rotation": days_old})
                except ValueError:
                    pass
        except Exception:
            # Skip individual secret read errors; we have enough signal from the rest
            continue

    if counted == 0:
        return KPIReading(
            persona="bt",
            kpi_name="secrets_rotation_compliance",
            status="no_data",
            error_detail="no secrets returned modifiedAt timestamps",
            raw_payload={"names_seen": len(names)},
        )

    pct = round(100.0 * compliant / counted, 2)
    return KPIReading(
        persona="bt",
        kpi_name="secrets_rotation_compliance",
        value_numeric=pct,
        unit="%",
        raw_payload={
            "rotation_window_days": _ROTATION_WINDOW_DAYS,
            "secrets_counted": counted,
            "compliant": compliant,
            "stale_count": len(stale),
            "stale_sample": stale[:10],  # top 10 most-stale for the brief
        },
    )
