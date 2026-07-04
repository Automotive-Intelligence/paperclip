"""services/twenty_schema.py -- Twenty schema enforcement (item 6).

Item 6 of the B&T flag posted 2026-07-03 in avo-telemetry/revenue_state.md.
Complete spec: avo-telemetry/marketing_deliverables/intent_workflow_spec_v1_2026-07-03.md
section 4 (CRM data model = the interoperability contract).

This module is the single source of truth for:

  1. TWENTY_PERSON_CUSTOM_FIELDS  -- 9 custom Person fields every workspace
     must carry (config_version, fit_score/band, intent_score/band, quadrant,
     activation_channel, compliance_status, consent_basis).
  2. TWENTY_SIGNAL_OBJECT_SCHEMA  -- the Signal custom object: one row per
     intent event. Lets S8 attribute revenue to signal-type and lets one
     person accumulate signals without overwriting a single Person field.
  3. TWENTY_PIPELINE_STAGES       -- the identical 8-stage pipeline every
     brand uses (brand is a field, not a separate pipeline).
  4. assert_schema(business_key)  -- probes a workspace, reports missing
     fields/objects. Non-destructive (never mutates schema).
  5. create_signal_record(...)    -- writes a Signal row after Person upsert.

Design notes:
  - Assertion is a DIFF REPORT, not an auto-migration. Twenty schema mutations
    are a founder-hands operation. The startup hook (services/twenty_schema.py::
    startup_check) logs the diff as a warning so operators SEE the gap; it does
    not crash the service or auto-create fields.
  - Signal writes go to the /rest/signals endpoint per Twenty v0.x convention.
    If the endpoint 404s (Signal object not created yet in this workspace),
    the writer returns status=skipped with a clear reason; the audit table in
    intent_inbound_events still holds the record for retry.
  - The 9 Person fields moved here from services/intent_scoring.py as the
    canonical location. intent_scoring.py re-exports them for backward compat.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical schema constants (spec section 4)
# ---------------------------------------------------------------------------

TWENTY_PERSON_CUSTOM_FIELDS: List[Dict[str, str]] = [
    {"name": "config_version",     "type": "TEXT",   "desc": "SHA-256 (first 16) of brand.yaml at scoring time."},
    {"name": "fit_score",          "type": "NUMBER", "desc": "Fit axis 0-100."},
    {"name": "fit_band",           "type": "TEXT",   "desc": "A/B/C/F."},
    {"name": "intent_score",       "type": "NUMBER", "desc": "Intent axis 0-100."},
    {"name": "intent_band",        "type": "TEXT",   "desc": "High/Med/Low."},
    {"name": "quadrant",           "type": "TEXT",   "desc": "ACT_NOW/NURTURE_HOT/WATCH/QUALIFY_CAUTION/NURTURE_LOW/SUPPRESS_SOFT/SUPPRESS."},
    {"name": "activation_channel", "type": "TEXT",   "desc": "channel_roster entry that fired (cold_email, direct_mail, etc.)."},
    {"name": "compliance_status",  "type": "TEXT",   "desc": "clear/opt_in/opt_out/dnc/suppressed/region_restricted."},
    {"name": "consent_basis",      "type": "TEXT",   "desc": "legitimate_interest/express_consent/existing_customer/none."},
]


# Signal custom object. One row per S1 intent event; a Person accumulates many
# Signal rows over its lifetime (spec section 4: "Signal-as-object lets S8
# attribute revenue to signal-type and lets one person accumulate signals
# without overwriting").
TWENTY_SIGNAL_OBJECT_SCHEMA: Dict[str, Any] = {
    "object_name":        "signal",
    "object_display":     "Signal",
    "fields": [
        {"name": "person_id",         "type": "RELATION", "desc": "FK to Person."},
        {"name": "brand",             "type": "TEXT",     "desc": "Brand slug (avi, wd, aipg, bookd, pp, panda)."},
        {"name": "channel",           "type": "TEXT",     "desc": "cold_email/warm_email/direct_mail/inbound_call/etc."},
        {"name": "response_type",     "type": "TEXT",     "desc": "8-value enum from services/intent_inbound.py."},
        {"name": "subtype",           "type": "TEXT",     "desc": "Free-form specialization within response_type."},
        {"name": "source_name",       "type": "TEXT",     "desc": "SignalSource.name from brand.yaml (permit_feed, intent_topics, etc.)."},
        {"name": "occurred_at",       "type": "DATE_TIME","desc": "When the event actually happened (sender-provided)."},
        {"name": "config_version",    "type": "TEXT",     "desc": "brand.yaml hash at ingest time."},
        {"name": "fit_score",         "type": "NUMBER",   "desc": "Score at this event (may differ from Person's latest)."},
        {"name": "intent_score",      "type": "NUMBER",   "desc": "Intent score at this event."},
        {"name": "quadrant",          "type": "TEXT",     "desc": "Quadrant at this event."},
        {"name": "idempotency_key",   "type": "TEXT",     "desc": "SHA-256 dedupe key; unique constraint."},
    ],
}


# The 8-stage pipeline is identical across brands (brand is a field, not a
# separate pipeline). Spec section 4:
#   Signal Captured -> Enriched -> Scored -> Activated -> Engaged ->
#   Meeting/Quote Set -> Opportunity -> Won/Lost/Nurture/Suppressed
TWENTY_PIPELINE_STAGES: List[str] = [
    "Signal Captured",
    "Enriched",
    "Scored",
    "Activated",
    "Engaged",
    "Meeting/Quote Set",
    "Opportunity",
    "Won",
    "Lost",
    "Nurture",
    "Suppressed",
]


# Namespaced tag prefixes per spec section 4 ("No free-text tags").
TWENTY_TAG_PREFIXES: List[str] = [
    "source:",
    "signal:",
    "score:",   # e.g. score:A-high
    "owner:",
    "brand:",
    "channel:",
    "compliance:",
    "campaign:",
]


# ---------------------------------------------------------------------------
# Schema assertion (diff report; NEVER mutates)
# ---------------------------------------------------------------------------


def assert_schema(business_key: str) -> Dict[str, Any]:
    """Probe a Twenty workspace, return a structured diff report:

        {
          "workspace_ok": bool,
          "person_fields": {"present": [...], "missing": [...], "extra_seen": [...]},
          "signal_object": {"present": bool, "missing_fields": [...]} | {"present": False},
          "pipeline_stages": {"present": [...], "missing": [...]} | None,
          "raw_errors": [...],
        }

    Non-destructive: this function makes GET requests only. Adding missing
    fields/objects is a founder-hands operation (Twenty admin UI or a separate
    apply_schema admin endpoint we can build later).
    """
    from tools.twenty import _workspace_config, _headers

    report: Dict[str, Any] = {
        "workspace_ok": False,
        "person_fields": {"present": [], "missing": [], "extra_seen": []},
        "signal_object": {"present": False, "missing_fields": []},
        "pipeline_stages": None,
        "raw_errors": [],
    }
    try:
        base_url, api_key = _workspace_config(business_key)
    except ValueError as e:
        report["raw_errors"].append(f"workspace config missing: {e}")
        return report
    report["workspace_ok"] = True

    # 1. Person field probe. Fetch one Person, inspect the returned attribute
    # keys. This is a soft probe -- Twenty's metadata API varies by version;
    # inspecting a real record is universal.
    expected = {f["name"] for f in TWENTY_PERSON_CUSTOM_FIELDS}
    try:
        r = requests.get(
            f"{base_url}/rest/people?limit=1",
            headers=_headers(api_key), timeout=15,
        )
        if r.ok:
            body = r.json()
            data = body.get("data") if isinstance(body, dict) else None
            records = (data or {}).get("people") if isinstance(data, dict) else None
            if records:
                seen = set(records[0].keys())
                present = sorted(expected & seen)
                missing = sorted(expected - seen)
                report["person_fields"]["present"] = present
                report["person_fields"]["missing"] = missing
            else:
                report["raw_errors"].append("no people records; cannot infer schema")
        else:
            report["raw_errors"].append(f"GET /rest/people HTTP {r.status_code}")
    except requests.RequestException as e:
        report["raw_errors"].append(f"GET /rest/people network: {e}")

    # 2. Signal object probe. Query /rest/signals; 404 = object not created.
    try:
        r = requests.get(
            f"{base_url}/rest/signals?limit=1",
            headers=_headers(api_key), timeout=15,
        )
        if r.status_code == 404:
            report["signal_object"]["present"] = False
            report["signal_object"]["missing_fields"] = [
                f["name"] for f in TWENTY_SIGNAL_OBJECT_SCHEMA["fields"]
            ]
        elif r.ok:
            report["signal_object"]["present"] = True
            body = r.json()
            data = body.get("data") if isinstance(body, dict) else None
            records = (data or {}).get("signals") if isinstance(data, dict) else None
            if records:
                seen = set(records[0].keys())
                expected_sig = {f["name"] for f in TWENTY_SIGNAL_OBJECT_SCHEMA["fields"]}
                report["signal_object"]["missing_fields"] = sorted(expected_sig - seen)
        else:
            report["raw_errors"].append(f"GET /rest/signals HTTP {r.status_code}")
    except requests.RequestException as e:
        report["raw_errors"].append(f"GET /rest/signals network: {e}")

    return report


# ---------------------------------------------------------------------------
# Signal writer
# ---------------------------------------------------------------------------


def create_signal_record(
    business_key: str,
    signal_row: Dict[str, Any],
) -> Dict[str, Any]:
    """Write a Signal record to the brand's Twenty workspace.

    signal_row must include the fields from TWENTY_SIGNAL_OBJECT_SCHEMA
    (person_id, brand, channel, response_type, occurred_at, config_version,
    idempotency_key, and optionally the scoring snapshot).

    Returns {"status": "created" | "skipped_object_missing" | "duplicate_skipped"
    | "error", ...}. Non-fatal on schema-missing (returns "skipped_object_missing"
    so the caller can persist to the audit table and retry once the schema is
    applied).
    """
    from tools.twenty import _workspace_config, _headers

    try:
        base_url, api_key = _workspace_config(business_key)
    except ValueError as e:
        return {"status": "error", "reason": f"workspace: {e}"}

    try:
        r = requests.post(
            f"{base_url}/rest/signals",
            headers=_headers(api_key),
            json={"data": signal_row},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"status": "error", "reason": f"network: {e}"}

    if r.status_code == 404:
        return {
            "status": "skipped_object_missing",
            "reason": "Signal custom object not present in this workspace. Apply schema.",
        }
    if r.status_code == 409:
        # Duplicate idempotency_key.
        return {"status": "duplicate_skipped", "http": 409}
    if not r.ok:
        return {"status": "error", "reason": f"HTTP {r.status_code} {r.text[:300]}"}
    body = r.json() if r.text.strip() else {}
    signal_id = ""
    try:
        signal_id = (body.get("data") or {}).get("createSignal", {}).get("id") or ""
    except Exception:
        pass
    return {"status": "created", "signal_id": signal_id}


# ---------------------------------------------------------------------------
# Person upsert with scoring stamp (extends push_prospects_to_twenty)
# ---------------------------------------------------------------------------


def upsert_person_with_score(
    business_key: str,
    person: Dict[str, Any],
    score_snapshot: Optional[Dict[str, Any]] = None,
    source_agent: str = "intent_workflow",
) -> Dict[str, Any]:
    """Upsert a Person via the existing push_prospects_to_twenty writer,
    then stamp the score_snapshot fields (config_version, fit/intent scores +
    bands, quadrant, activation_channel, compliance_status, consent_basis)
    onto the Person via a PATCH.

    Returns the Twenty writer's per-prospect result plus a "score_stamped"
    boolean indicating whether the custom fields update succeeded.
    """
    try:
        from tools.twenty import push_prospects_to_twenty, _workspace_config, _headers
    except ImportError as e:
        return {"status": "error", "reason": f"twenty import: {e}"}

    results = push_prospects_to_twenty(
        [person], source_agent=source_agent, business_key=business_key,
    )
    if not results:
        return {"status": "twenty_empty", "score_stamped": False}
    r0 = dict(results[0])  # copy
    person_id = r0.get("contact_id")
    if not person_id or not score_snapshot:
        r0["score_stamped"] = False
        return r0

    # Stamp the score fields via PATCH.
    try:
        base_url, api_key = _workspace_config(business_key)
        allowed = {f["name"] for f in TWENTY_PERSON_CUSTOM_FIELDS}
        payload = {k: v for k, v in score_snapshot.items() if k in allowed}
        if not payload:
            r0["score_stamped"] = False
            r0["stamp_reason"] = "no known fields in score_snapshot"
            return r0
        resp = requests.patch(
            f"{base_url}/rest/people/{person_id}",
            headers=_headers(api_key), json={"data": payload}, timeout=30,
        )
        r0["score_stamped"] = resp.ok
        if not resp.ok:
            r0["stamp_reason"] = f"HTTP {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        r0["score_stamped"] = False
        r0["stamp_reason"] = f"{type(e).__name__}: {e}"
    return r0


# ---------------------------------------------------------------------------
# Startup hook -- log a diff report for each mapped workspace.
# ---------------------------------------------------------------------------


def startup_check(business_keys: Optional[List[str]] = None) -> None:
    """Called from app.py at boot. Logs a per-workspace schema diff as a
    warning so operators SEE the gap. Never fail-crashes -- Twenty schema is
    a founder-hands mutation and we don't block startup on it.

    Skip if TWENTY_STARTUP_CHECK=0 in env.
    """
    if (os.getenv("TWENTY_STARTUP_CHECK") or "").strip() == "0":
        return
    from tools.twenty import _WORKSPACE_KEY_ENV
    if business_keys is None:
        business_keys = list(_WORKSPACE_KEY_ENV.keys())
    for bk in business_keys:
        try:
            report = assert_schema(bk)
        except Exception as e:
            logger.warning("[twenty_schema] startup_check '%s' raised: %s", bk, e)
            continue
        if not report.get("workspace_ok"):
            logger.info("[twenty_schema] %s workspace not configured; skipping", bk)
            continue
        missing_person = report["person_fields"]["missing"]
        signal_present = report["signal_object"]["present"]
        if missing_person:
            logger.warning(
                "[twenty_schema] %s: Person MISSING %d custom fields: %s",
                bk, len(missing_person), missing_person,
            )
        if not signal_present:
            logger.warning(
                "[twenty_schema] %s: Signal custom object MISSING; "
                "Signal writes will skip until schema applied",
                bk,
            )
        if not missing_person and signal_present:
            logger.info("[twenty_schema] %s: schema OK", bk)


__all__ = [
    "TWENTY_PERSON_CUSTOM_FIELDS",
    "TWENTY_SIGNAL_OBJECT_SCHEMA",
    "TWENTY_PIPELINE_STAGES",
    "TWENTY_TAG_PREFIXES",
    "assert_schema",
    "create_signal_record",
    "upsert_person_with_score",
    "startup_check",
]
