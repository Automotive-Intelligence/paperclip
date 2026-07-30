"""Persona decision runbooks — one YAML per persona.

Loaded by services/persona_wake.py. Each runbook maps (KPI name, status)
to a bounded list of candidate levers the persona is allowed to execute.
The persona wake loop consults these instead of letting the Claude session
invent actions freeform — an allowlist gate for autonomous execution.

Schema is documented in `bt.yaml` (Phase C1's first runbook). CRO + CMO
land in C4; the other 6 personas fan out in C5.
"""
