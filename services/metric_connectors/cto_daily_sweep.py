"""cto_daily_sweep connector — reuses existing infrastructure_sweep checks.

Source for B&T scorecard KPIs:
  - domain_ssl_green_rate   — % of monitored domains with cert >30d from expiry
  - api_uptime_per_service  — % of critical service URLs returning 2xx/3xx

Reuses `check_domain_ssl()` and `check_app_health()` directly from
services.infrastructure_sweep so the two surfaces never drift on what counts
as "green" — same monitored domain/URL lists, same expiry thresholds, same
HTTP timeout, same UA spoof for Twenty's CF challenge.

Health check cost: ~300-800ms per cycle. Fits comfortably in the connector
30s budget. SSL check costs ~100ms per domain (cert read, no full handshake).
"""

from typing import List

from services.infrastructure_sweep import (
    SSL_EXPIRY_WARN_DAYS,
    check_app_health,
    check_domain_ssl,
)
from services.metric_connectors.types import KPIReading


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name == "domain_ssl_green_rate":
        return [_domain_ssl_green_rate()]
    if name == "api_uptime_per_service":
        return [_api_uptime_per_service()]
    raise ValueError(f"cto_daily_sweep: unsupported kpi {name!r}")


def _domain_ssl_green_rate() -> KPIReading:
    """% of monitored domains with cert >SSL_EXPIRY_WARN_DAYS (30d) from expiry.

    Reuses check_domain_ssl() which returns Findings of severity info/warn/critical.
    Green = severity 'info'. Yellow + Red drag the rate down. Total denominator
    is the monitored-domain list size from infrastructure_sweep.MONITORED_DOMAINS.
    """
    from services.infrastructure_sweep import MONITORED_DOMAINS

    findings = check_domain_ssl()
    total = len(MONITORED_DOMAINS)
    if total == 0:
        return KPIReading(persona="bt", kpi_name="domain_ssl_green_rate", status="no_data")

    not_green = sum(1 for f in findings if f.severity in ("warn", "critical"))
    green = total - not_green
    pct = round(100.0 * green / total, 2)

    return KPIReading(
        persona="bt",
        kpi_name="domain_ssl_green_rate",
        value_numeric=pct,
        unit="%",
        raw_payload={
            "total_domains": total,
            "green_domains": green,
            "warn_or_critical": not_green,
            "expiry_warn_threshold_days": SSL_EXPIRY_WARN_DAYS,
            "findings_titles": [f.title for f in findings if f.severity in ("warn", "critical")],
        },
    )


def _api_uptime_per_service() -> KPIReading:
    """% of monitored critical service URLs returning 2xx/3xx in latest check.

    check_app_health() returns Findings only for FAILED required URLs (per
    sweep design — silent on success). So we need the total denominator from
    MONITORED_HEALTH_URLS where required=True, and count failures = findings.
    """
    from services.infrastructure_sweep import MONITORED_HEALTH_URLS

    required = [u for u in MONITORED_HEALTH_URLS if len(u) >= 3 and u[2]]
    total = len(required)
    if total == 0:
        return KPIReading(persona="bt", kpi_name="api_uptime_per_service", status="no_data")

    findings = check_app_health()
    # Findings from health check are emitted only for failing required URLs.
    failed = sum(1 for f in findings if "[health]" in (f.check or "").lower() or f.check == "health")
    # Belt-and-suspenders: also count by severity in case the check name varies
    if failed == 0:
        failed = sum(1 for f in findings if f.severity == "critical")

    up = total - failed
    pct = round(100.0 * up / total, 2)
    return KPIReading(
        persona="bt",
        kpi_name="api_uptime_per_service",
        value_numeric=pct,
        unit="%",
        raw_payload={
            "total_required_urls": total,
            "up": up,
            "failed": failed,
            "checked_urls": [u[0] for u in required],
        },
    )
