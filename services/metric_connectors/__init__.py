"""Metric connectors — one module per named source in persona_scorecards/*.yaml.

Each module exports:

    def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
        ...

Returning an empty list = "no data this cycle" (collector writes status='no_data').
Raising = "connector down" (collector writes status='connector_down' + the
exception message; collector cycle continues).

Connector modules must be cheap to import (load on demand). Heavy clients
(requests sessions, DB pools) instantiate inside fetch(), not at import time.
"""
