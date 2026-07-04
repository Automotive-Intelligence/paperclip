"""stripe_arr connector — AVO MRR for the CRO scorecard.

Backs the KPI declared in services/persona_scorecards/cro.yaml:

    - name: avo_mrr
      source: stripe_arr

Sums the monthly-normalized amount of all active Stripe subscriptions
(tools.stripe.active_subscription_mrr). This is the recurring get-paid rail's
top-line number.

GUARDED — degrades cleanly, never crashes the metrics collector:
  - No STRIPE_SECRET_KEY / SDK  -> status='connector_down', value 0, with a
    reason. (Pre-activation state. The collector already tolerates
    connector_down; this just makes the reason explicit instead of a generic
    "module not implemented yet".)
  - Live but zero active subs   -> status='ok', value 0.0 (real pre-revenue).
  - Stripe/API error            -> status='connector_down' with the detail.

Contract mirrors the sibling connectors: fetch(kpi_spec, run_ctx) -> [KPIReading].
avo_mrr is org-level (not per_brand), so this returns exactly one reading.
"""

from typing import List

from services.metric_connectors.types import KPIReading


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or "avo_mrr"

    # Import inside fetch so a not-yet-installed `stripe` SDK cannot break
    # module import for the whole collector (connectors must be cheap to import).
    try:
        from tools.stripe import stripe_ready, active_subscription_mrr
    except Exception as e:  # pragma: no cover - defensive
        return [KPIReading(
            persona="cro", kpi_name=name, brand=None,
            status="connector_down", value_numeric=0.0, unit="USD",
            error_detail=f"stripe helper import failed: {str(e)[:300]}",
        )]

    if not stripe_ready():
        return [KPIReading(
            persona="cro", kpi_name=name, brand=None,
            status="connector_down", value_numeric=0.0, unit="USD",
            error_detail="Stripe inactive (STRIPE_SECRET_KEY not set). "
                         "MRR rail awaits activation.",
        )]

    try:
        mrr = active_subscription_mrr()
    except Exception as e:
        return [KPIReading(
            persona="cro", kpi_name=name, brand=None,
            status="connector_down", value_numeric=0.0, unit="USD",
            error_detail=f"Stripe MRR query failed: {str(e)[:300]}",
        )]

    return [KPIReading(
        persona="cro", kpi_name=name, brand=None,
        status="ok", value_numeric=float(mrr), unit="USD",
        raw_payload={"active_subscription_mrr_usd": mrr},
    )]
