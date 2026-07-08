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
    # keys. Twenty stores our canonical snake_case names as camelCase, so we
    # normalize each observed key by converting it back to snake_case AND
    # keeping the raw form for comparison.
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
                raw = set(records[0].keys())
                # Twenty responds with camelCase; normalize each observed key
                # by adding the snake_case equivalent so our canonical names
                # match either shape.
                seen: set = set(raw)
                for k in raw:
                    seen.add(_snake_from_camel(k))
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
                raw = set(records[0].keys())
                seen = set(raw)
                for k in raw:
                    seen.add(_snake_from_camel(k))
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

    # Convert our snake_case keys to Twenty's camelCase field names.
    # Twenty's REST v0.x /rest/{object}s expects the fields FLAT in the body;
    # wrapping in {"data": {...}} makes Twenty treat "data" as a field name
    # and reject with "Object signal doesn't have any 'data' field".
    data = {_to_camel(k): v for k, v in signal_row.items()}

    # Twenty stored our RELATION field as name="personId" with
    # joinColumnName="personIdId" (its convention: append "Id" to the field
    # name to derive the FK column). Neither the connect wrapper on the
    # field name nor a flat write to `personId` actually attaches the
    # relation -- Twenty accepts the request but silently drops the FK.
    # The write MUST target the join column directly: `personIdId: <uuid>`.
    # (See metadata GET /rest/metadata/fields for the Signal object; the
    #  personId field's settings.joinColumnName confirms this convention.)
    person_fk = data.pop("personId", None)
    if isinstance(person_fk, str) and person_fk:
        data["personIdId"] = person_fk

    try:
        r = requests.post(
            f"{base_url}/rest/signals",
            headers=_headers(api_key),
            json=data,
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
# Schema APPLY (creates missing Person fields + Signal object via metadata API)
#
# Twenty CRM's REST metadata API lives at /rest/metadata (v0.x convention).
# Endpoints exercised here:
#   GET  /rest/metadata/objects                     -> list all objects
#   POST /rest/metadata/objects                     -> create custom object
#   GET  /rest/metadata/objects/{id}/fields         -> list fields on an object
#   POST /rest/metadata/objects/{id}/fields         -> create custom field
#
# If a specific Twenty instance exposes these at slightly different paths, the
# apply_schema function returns structured errors per operation so the caller
# can either patch the paths here or fall back to the admin UI.
#
# Non-destructive: skip if field/object already exists (probed via metadata
# list). Never deletes / modifies existing fields.
# ---------------------------------------------------------------------------


# Map our canonical field type names -> Twenty's field type enum. Twenty v0.x
# uses these type names in the metadata API. If a specific version varies, the
# per-field POST will surface a clear error the caller can iterate on.
_TWENTY_FIELD_TYPE_MAP: Dict[str, str] = {
    "TEXT":      "TEXT",
    "NUMBER":    "NUMBER",
    "DATE_TIME": "DATE_TIME",
    "RELATION":  "RELATION",
    "BOOLEAN":   "BOOLEAN",
    "SELECT":    "SELECT",
}


def _metadata_get(base_url: str, api_key: str, path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """GET against Twenty's metadata surface. Returns (body, error)."""
    try:
        r = requests.get(f"{base_url}{path}", headers=_headers_for(api_key), timeout=20)
    except requests.RequestException as e:
        return None, f"network: {e}"
    if r.status_code == 404:
        return None, "endpoint 404 (metadata API path may differ in this Twenty version)"
    if not r.ok:
        return None, f"HTTP {r.status_code} {r.text[:200]}"
    try:
        return r.json(), None
    except ValueError:
        return None, f"non-JSON body: {r.text[:200]}"


def _metadata_post(base_url: str, api_key: str, path: str, body: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """POST against Twenty's metadata surface. Returns (body, error)."""
    try:
        r = requests.post(
            f"{base_url}{path}",
            headers=_headers_for(api_key),
            json=body,
            timeout=30,
        )
    except requests.RequestException as e:
        return None, f"network: {e}"
    if r.status_code == 409:
        return None, "conflict (already exists)"
    if r.status_code == 404:
        return None, "endpoint 404 (metadata API path may differ in this Twenty version)"
    if not r.ok:
        return None, f"HTTP {r.status_code} {r.text[:300]}"
    try:
        return r.json(), None
    except ValueError:
        return {"raw": r.text[:200]}, None


def _headers_for(api_key: str) -> Dict[str, str]:
    """Local copy of the headers helper -- avoids importing from tools.twenty
    (which is optional at module load time to keep tests light)."""
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _extract_objects(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Twenty's metadata list responses can nest under data.objects or return
    a bare list; handle both."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        data = body.get("data") or body.get("objects")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            objs = data.get("objects") or data.get("objectMetadataItems") or data.get("items")
            if isinstance(objs, list):
                return objs
    return []


def _extract_fields(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Twenty's field-list response shape parallel to _extract_objects."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        data = body.get("data") or body.get("fields")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            fields = data.get("fields") or data.get("fieldMetadataItems") or data.get("items")
            if isinstance(fields, list):
                return fields
    return []


def _find_object_id(objects: List[Dict[str, Any]], name_or_target: str) -> Optional[str]:
    """Find an object's id by matching name / nameSingular / namePlural /
    targetTableName. Twenty normalizes these differently across versions."""
    target = name_or_target.lower()
    for o in objects:
        if not isinstance(o, dict):
            continue
        for key in ("nameSingular", "namePlural", "name", "targetTableName"):
            v = str(o.get(key) or "").lower()
            if v == target or v == target + "s":
                return o.get("id")
    return None


def _existing_field_names(fields: List[Dict[str, Any]]) -> set:
    """Return the set of field name strings already present on an object.

    Twenty stores field names in camelCase; we canonicalize to snake_case
    internally, so this returns BOTH the raw camelCase name and the
    snake_case equivalent so callers can match against either.
    """
    names: set = set()
    for f in fields:
        if not isinstance(f, dict):
            continue
        for key in ("name", "nameSingular"):
            v = f.get(key)
            if v:
                camel = str(v)
                names.add(camel)
                names.add(_snake_from_camel(camel))
    return names


def _to_camel(snake: str) -> str:
    """Convert snake_case to camelCase for Twenty's field names.

    Twenty rejects any name containing underscores with a clear validation
    error: "Name is not valid: it must start with lowercase letter and
    contain only alphanumeric characters." Confirmed 2026-07-07 against
    a live workspace.
    """
    parts = snake.split("_")
    if not parts:
        return snake
    return parts[0] + "".join(p.title() for p in parts[1:])


def _snake_from_camel(camel: str) -> str:
    """Reverse of _to_camel; mirrored for existence probes."""
    out = []
    for ch in camel:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


# HTTP 400 responses whose body signals "already exists" so the caller can
# treat as skip instead of error. Twenty returns 400 with code=NOT_AVAILABLE
# when a field name collides.
def _is_already_exists_error(err: str) -> bool:
    if not err:
        return False
    if "NOT_AVAILABLE" in err and "is not available" in err:
        return True
    if "already used by another field" in err:
        return True
    return False


def apply_schema(
    business_key: str,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create missing Person custom fields + Signal object + Signal fields in
    the brand's Twenty workspace. Non-destructive (skips existing).

    Returns a structured report:
      {
        "workspace_ok": bool,
        "dry_run": bool,
        "person": {
          "object_id": str | None,
          "fields_created": [...],
          "fields_skipped_existing": [...],
          "fields_errors": [{name, error}]
        },
        "signal": {
          "object_id": str | None,
          "object_created": bool,
          "object_skipped_existing": bool,
          "object_error": str | None,
          "fields_created": [...],
          "fields_skipped_existing": [...],
          "fields_errors": [{name, error}]
        },
        "raw_errors": [...]
      }

    Caller (admin endpoint) is expected to be Bearer-authed. This function does
    NOT validate auth on its own.
    """
    from tools.twenty import _workspace_config

    report: Dict[str, Any] = {
        "workspace_ok": False,
        "dry_run": dry_run,
        "person": {
            "object_id": None,
            "fields_created": [],
            "fields_skipped_existing": [],
            "fields_errors": [],
        },
        "signal": {
            "object_id": None,
            "object_created": False,
            "object_skipped_existing": False,
            "object_error": None,
            "fields_created": [],
            "fields_skipped_existing": [],
            "fields_errors": [],
        },
        "raw_errors": [],
    }

    try:
        base_url, api_key = _workspace_config(business_key)
    except ValueError as e:
        report["raw_errors"].append(f"workspace config: {e}")
        return report
    report["workspace_ok"] = True

    # 1. List all objects; find Person, and see if Signal already exists.
    objs_body, err = _metadata_get(base_url, api_key, "/rest/metadata/objects")
    if err:
        report["raw_errors"].append(f"list objects: {err}")
        return report
    objects = _extract_objects(objs_body or {})
    person_id = _find_object_id(objects, "person")
    signal_id = _find_object_id(objects, "signal")
    report["person"]["object_id"] = person_id
    if not person_id:
        report["raw_errors"].append("Person object not found in metadata list; cannot add fields")
        # We can still try to create Signal.

    # 2. Person custom fields.
    # NOTE: Twenty's REST metadata API is FLAT, not nested. Fields live at
    # /rest/metadata/fields, with objectMetadataId in the query (GET) or the
    # body (POST). The nested path /rest/metadata/objects/{id}/fields returns
    # 400 with a clear invalid-path hint. Confirmed against a live workspace
    # 2026-07-07.
    if person_id:
        fields_body, err = _metadata_get(
            base_url, api_key,
            f"/rest/metadata/fields?objectMetadataId={person_id}",
        )
        if err:
            report["raw_errors"].append(f"list person fields: {err}")
            existing = set()
        else:
            existing = _existing_field_names(_extract_fields(fields_body or {}))
        for f in TWENTY_PERSON_CUSTOM_FIELDS:
            name = f["name"]
            camel = _to_camel(name)
            if name in existing or camel in existing:
                report["person"]["fields_skipped_existing"].append(name)
                continue
            if dry_run:
                report["person"]["fields_created"].append(name + " (dry-run)")
                continue
            body = {
                "name": camel,
                "type": _TWENTY_FIELD_TYPE_MAP.get(f["type"], f["type"]),
                "objectMetadataId": person_id,
                "description": f.get("desc", ""),
                "isNullable": True,
                "label": name.replace("_", " ").title(),
            }
            resp, err2 = _metadata_post(
                base_url, api_key, "/rest/metadata/fields", body,
            )
            if err2 == "conflict (already exists)" or _is_already_exists_error(err2 or ""):
                report["person"]["fields_skipped_existing"].append(name)
            elif err2:
                report["person"]["fields_errors"].append({"name": name, "error": err2})
            else:
                report["person"]["fields_created"].append(name)

    # 3. Signal object.
    if not signal_id:
        obj_spec = TWENTY_SIGNAL_OBJECT_SCHEMA
        if dry_run:
            report["signal"]["object_created"] = True
            report["signal"]["object_id"] = "(dry-run)"
            # In dry-run we still list all fields as "created" so the caller
            # sees the shape the endpoint WOULD apply.
            report["signal"]["fields_created"] = [f["name"] for f in obj_spec["fields"]]
        else:
            create_body = {
                "nameSingular":  obj_spec["object_name"],
                "namePlural":    obj_spec["object_name"] + "s",
                "labelSingular": obj_spec["object_display"],
                "labelPlural":   obj_spec["object_display"] + "s",
                "description":   "One row per S1 intent event (spec section 4).",
                "isCustom":      True,
            }
            resp, err3 = _metadata_post(
                base_url, api_key, "/rest/metadata/objects", create_body,
            )
            if err3 == "conflict (already exists)":
                report["signal"]["object_skipped_existing"] = True
                # Re-list to pick up the id.
                objs_body2, _ = _metadata_get(base_url, api_key, "/rest/metadata/objects")
                signal_id = _find_object_id(_extract_objects(objs_body2 or {}), "signal")
                report["signal"]["object_id"] = signal_id
            elif err3:
                report["signal"]["object_error"] = err3
            else:
                report["signal"]["object_created"] = True
                # Extract id from response
                created = None
                if isinstance(resp, dict):
                    data = resp.get("data") or resp
                    if isinstance(data, dict):
                        created = data.get("createObject") or data
                        if isinstance(created, dict):
                            signal_id = created.get("id")
                report["signal"]["object_id"] = signal_id

    else:
        report["signal"]["object_skipped_existing"] = True
        report["signal"]["object_id"] = signal_id

    # 4. Signal object fields (same flat path pattern as Person fields above).
    if signal_id and not dry_run:
        fields_body, err4 = _metadata_get(
            base_url, api_key,
            f"/rest/metadata/fields?objectMetadataId={signal_id}",
        )
        existing_sig = _existing_field_names(_extract_fields(fields_body or {})) if not err4 else set()
        for f in TWENTY_SIGNAL_OBJECT_SCHEMA["fields"]:
            name = f["name"]
            camel = _to_camel(name)
            if name in existing_sig or camel in existing_sig:
                report["signal"]["fields_skipped_existing"].append(name)
                continue
            body = {
                "name": camel,
                "type": _TWENTY_FIELD_TYPE_MAP.get(f["type"], f["type"]),
                "objectMetadataId": signal_id,
                "description": f.get("desc", ""),
                "isNullable": True,
                "label": name.replace("_", " ").title(),
            }
            # RELATION fields need a relationCreationPayload block (Twenty's
            # documented shape; 'settings' is silently ignored). Point Signal.
            # person_id at the Person object we found above; use MANY_TO_ONE
            # per spec section 4 (a Person accumulates many Signals).
            if f["type"] == "RELATION" and person_id:
                body["relationCreationPayload"] = {
                    "type": "MANY_TO_ONE",
                    "targetObjectMetadataId": person_id,
                    "targetFieldLabel": "Signals",
                    "targetFieldIcon": "IconLink",
                }
            # UNIQUE constraint on idempotency_key (spec section 4 dedup)
            if name == "idempotency_key":
                body["isUnique"] = True

            resp, err5 = _metadata_post(
                base_url, api_key, "/rest/metadata/fields", body,
            )
            if err5 == "conflict (already exists)" or _is_already_exists_error(err5 or ""):
                report["signal"]["fields_skipped_existing"].append(name)
            elif err5:
                report["signal"]["fields_errors"].append({"name": name, "error": err5})
            else:
                report["signal"]["fields_created"].append(name)

    return report


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
    # Twenty stores custom field names in camelCase; our score_snapshot uses
    # snake_case for Pythonic consistency, so translate on write.
    try:
        base_url, api_key = _workspace_config(business_key)
        allowed = {f["name"] for f in TWENTY_PERSON_CUSTOM_FIELDS}
        payload = {
            _to_camel(k): v for k, v in score_snapshot.items() if k in allowed
        }
        if not payload:
            r0["score_stamped"] = False
            r0["stamp_reason"] = "no known fields in score_snapshot"
            return r0
        # Twenty PATCH expects flat body (same as POST); the {"data": ...}
        # wrapper is a response-side artifact, not a request-side one.
        resp = requests.patch(
            f"{base_url}/rest/people/{person_id}",
            headers=_headers(api_key), json=payload, timeout=30,
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
    "apply_schema",
    "assert_schema",
    "create_signal_record",
    "upsert_person_with_score",
    "startup_check",
]
