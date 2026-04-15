"""
tools/attio.py - Attio CRM connector for Paperclip sales pipeline.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import os
import logging
import smtplib
import re
from email.message import EmailMessage
from tools.outbound_email import email_delivery_mode, send_unified_email
from tools.email_templates import compose_templated_email, strict_template_validation_enabled
from tools.revenue_tracker import already_emailed

from services.errors import ServiceCallError
from services.http_client import request_with_retry

ATTIO_BASE_URL = "https://api.attio.com/v2"


_SOURCE_ANNOTATION_RE = re.compile(r"\s*\[source:[^\]]*\]\s*", re.IGNORECASE)


def _strip_source_annotation(text: str) -> str:
    """Remove inline `[source: <url>]` annotations from a fact string.

    Marcus's verified_fact strings sometimes arrive from the LLM with an
    appended `[source: <url>]` citation, and the paperclip pipeline used
    to add its own on top. When that text is used as a merge tag inside a
    cold email body (`{{company.cd_prospect_notes}}`), the `[source: ...]`
    literal renders in the recipient's inbox and reads noisy. The source
    URL is still preserved on the company's `website` attribute and in
    Postgres `persist_log` entries, so stripping it here loses nothing.
    """
    if not text:
        return text
    cleaned = _SOURCE_ANNOTATION_RE.sub(" ", text)
    return " ".join(cleaned.split()).strip()


def _normalize_person_name(contact_name: str, business_name: str) -> tuple[str, str, str]:
    """Return a safe (first_name, last_name, full_name) tuple for Attio person records.

    Attio's people.name attribute expects first_name, last_name, and full_name.
    Agent outputs can include placeholders or noisy text, so we sanitize and fall back
    to business tokens when needed.
    """
    raw = (contact_name or "").strip()

    # Treat common placeholders as missing names.
    lowered = raw.lower()
    if lowered.startswith("not found") or lowered.startswith("n/a") or lowered.startswith("unknown"):
        raw = ""

    # Keep only alphabetic name tokens (allow apostrophes/hyphens in words).
    source = raw if raw else (business_name or "")
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", source)

    # Ensure we always send at least two tokens.
    if len(tokens) >= 2:
        first_name = tokens[0]
        last_name = " ".join(tokens[1:3])
    elif len(tokens) == 1:
        first_name = tokens[0]
        last_name = "Prospect"
    else:
        first_name = "Sales"
        last_name = "Prospect"

    full_name = f"{first_name} {last_name}".strip()
    return first_name, last_name, full_name


def _attio_token() -> str:
    return (os.getenv("ATTIO_API_KEY") or "").strip()


def attio_ready() -> bool:
    return bool(_attio_token())


def attio_email_ready() -> bool:
    """Attio email sending is enabled when SMTP settings are fully configured."""
    required = (
        os.getenv("ATTIO_SMTP_HOST", "").strip(),
        os.getenv("ATTIO_SMTP_PORT", "").strip(),
        os.getenv("ATTIO_SMTP_USERNAME", "").strip(),
        os.getenv("ATTIO_SMTP_PASSWORD", "").strip(),
        os.getenv("ATTIO_SMTP_FROM", "").strip(),
    )
    return bool(attio_ready() and all(required))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_attio_token()}",
        "Content-Type": "application/json",
    }


def _attio_request(operation: str, method: str, path: str, *, json_body: dict | None = None, timeout: int = 15) -> dict:
    response = request_with_retry(
        provider="attio",
        operation=operation,
        method=method,
        url=f"{ATTIO_BASE_URL}{path}",
        headers=_headers(),
        json_body=json_body,
        timeout=timeout,
        max_attempts=3,
        backoff_seconds=0.7,
    )
    if response.ok:
        return response.data or {}
    if response.error is None:
        raise RuntimeError(f"attio.{operation} failed with unknown error")
    raise ServiceCallError(response.error)


def _search_company_by_name(name: str) -> str | None:
    """Return existing record_id if a company with this name exists, else None."""
    if not name:
        return None
    payload = {
        "filter": {"name": {"$equals": name}},
        "limit": 1,
    }
    try:
        data = _attio_request("search_company", "POST", "/objects/companies/records/query", json_body=payload, timeout=15)
        records = data.get("data", [])
        if not records:
            return None
        rec = records[0]
        return rec.get("id", {}).get("record_id") if isinstance(rec.get("id"), dict) else None
    except Exception:
        return None  # fail open — attempt create rather than silently skip


def _search_person_by_email(email: str) -> str | None:
    """Return existing record_id if a person with this email exists, else None."""
    if not email:
        return None
    payload = {
        "filter": {"email_addresses": {"$contains": email}},
        "limit": 1,
    }
    try:
        data = _attio_request("search_person", "POST", "/objects/people/records/query", json_body=payload, timeout=15)
        records = data.get("data", [])
        if not records:
            return None
        rec = records[0]
        return rec.get("id", {}).get("record_id") if isinstance(rec.get("id"), dict) else None
    except Exception:
        return None  # fail open


def _create_company_record(prospect: dict, source_agent: str, business_key: str) -> str:
    from datetime import date as _date

    business_name = prospect.get("business_name", "Unknown")
    website = (prospect.get("website") or "").strip()

    # Attio values are arrays of value objects for many system fields.
    values = {
        "name": [{"value": business_name}],
        "description": [{"value": (
            f"{source_agent} prospecting: {prospect.get('reason', '')} | "
            f"City: {prospect.get('city', '')} | "
            f"Phone: {prospect.get('phone', '')} | "
            f"Website: {website} | "
            f"Contact: {prospect.get('contact_name', '')}"
        )}],
    }

    # Marcus prospects include full cd_* fields for sequence merge fields
    if source_agent == "marcus" and business_key == "callingdigital":
        # Domain from website
        domain = ""
        if website:
            d = website.lower()
            for prefix in ("https://", "http://", "www."):
                if d.startswith(prefix):
                    d = d[len(prefix):]
            domain = d.split("/")[0].split("?")[0]
        if domain:
            values["domains"] = [{"domain": domain}]

        # cd_* fields for sequence merge
        vertical_slug = (prospect.get("vertical") or "").strip()
        industry_label = {
            "med-spa": "med spa", "pi-law": "personal injury law",
            "real-estate": "real estate", "home-builder": "custom home building",
        }.get(vertical_slug, prospect.get("business_type", ""))

        values["cd_industry"] = [{"value": industry_label}]
        values["cd_source_agent"] = [{"value": f"marcus-{vertical_slug}"}]
        values["cd_prospected_date"] = [{"value": _date.today().isoformat()}]
        values["cd_outreach_stage"] = [{"value": "imported"}]

        # cd_prospect_notes — verified fact for email personalization.
        # Strip any `[source: ...]` annotations so the merge tag renders
        # cleanly in outbound cold emails. Source URL is still preserved
        # on the company's website attribute and in Postgres logs.
        verified_fact = _strip_source_annotation((prospect.get("verified_fact") or "").strip())
        if verified_fact:
            values["cd_prospect_notes"] = [{"value": verified_fact}]
        else:
            trigger_event = _strip_source_annotation((prospect.get("trigger_event") or "").strip())
            if trigger_event:
                values["cd_prospect_notes"] = [{"value": trigger_event}]

    payload = {"data": {"values": values}}
    data = _attio_request("create_company_record", "POST", "/objects/companies/records", json_body=payload, timeout=15)
    record = data.get("data", {})
    return record.get("id", {}).get("record_id", "") if isinstance(record.get("id"), dict) else record.get("id", "")


def _create_person_record(prospect: dict, source_agent: str, business_key: str) -> str:
    email = (prospect.get("email") or "").strip()
    # Prefer the enriched contact_name over business_name for person records
    contact_name = (prospect.get("contact_name") or "").strip()
    business_name = (prospect.get("business_name") or "Unknown").strip()
    first_name, last_name, display_name = _normalize_person_name(contact_name, business_name)

    # Build description from available research fields
    desc_parts = [f"Company: {business_name}"]
    if prospect.get("trigger_event"):
        desc_parts.append(f"Trigger: {prospect['trigger_event']}")
    if prospect.get("reason"):
        desc_parts.append(f"{source_agent} prospecting: {prospect['reason']}")
    if prospect.get("phone"):
        desc_parts.append(f"Phone: {prospect['phone']}")
    if prospect.get("website"):
        desc_parts.append(f"Website: {prospect['website']}")
    description = " | ".join(desc_parts)

    payload = {
        "data": {
            "values": {
                "name": [{
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": display_name,
                }],
                # Attio email_addresses entries require the key "email_address", not "value"
                "email_addresses": [{"email_address": email}] if email else [],
                "description": [{"value": description}],
            }
        }
    }

    # Marcus prospects include vertical tag + company link for Attio workflows
    if source_agent == "marcus" and business_key == "callingdigital":
        vertical = (prospect.get("vertical") or "").strip()
        if vertical:
            payload["data"]["values"]["vertical"] = vertical

        # Link to Company record created in push_prospects_to_attio
        company_id = (prospect.get("_attio_company_id") or "").strip()
        if company_id:
            payload["data"]["values"]["company"] = [{
                "target_object": "companies",
                "target_record_id": company_id,
            }]

    data = _attio_request("create_person_record", "POST", "/objects/people/records", json_body=payload, timeout=15)
    record = data.get("data", {})
    return record.get("id", {}).get("record_id", "") if isinstance(record.get("id"), dict) else record.get("id", "")


def _touch_person_pipeline_stage(record_id: str, stage: str = "ownerphones-warm") -> bool:
    if not record_id:
        return False
    payload = {
        "data": {
            "values": {
                "pipeline_stage": stage,
            }
        }
    }
    try:
        _attio_request(
            "touch_person_pipeline_stage",
            "PATCH",
            f"/objects/people/records/{record_id}",
            json_body=payload,
            timeout=15,
        )
        return True
    except Exception as e:
        logging.warning("[Attio] Failed to touch pipeline_stage for %s: %s", record_id, e)
        return False


def _send_email_via_smtp(prospect: dict) -> bool:
    """Send first-touch email using configured SMTP for Attio-routed prospects."""
    to_email = (prospect.get("email") or "").strip()
    subject = (prospect.get("subject") or "").strip()
    body = (prospect.get("body") or "").strip()
    if not to_email or not subject or not body or not attio_email_ready():
        return False

    smtp_host = os.getenv("ATTIO_SMTP_HOST", "").strip()
    smtp_port = int((os.getenv("ATTIO_SMTP_PORT", "587") or "587").strip())
    smtp_user = os.getenv("ATTIO_SMTP_USERNAME", "").strip()
    smtp_pass = os.getenv("ATTIO_SMTP_PASSWORD", "").strip()
    from_email = os.getenv("ATTIO_SMTP_FROM", "").strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        logging.warning("[Attio] SMTP send failed for %s: %s", to_email, e)
        return False


def push_prospects_to_attio(prospects: list, source_agent: str = "marcus", business_key: str = "callingdigital") -> list:
    """Push parsed prospects to Attio records.

    For Marcus (callingdigital): Creates Company first (with cd_* fields), then Person
    linked to that Company (with vertical tag). This triggers the Attio workflow
    which auto-enrolls the Person into the correct email sequence.

    For other agents: If prospect has email, create person record. Otherwise company only.
    """
    if not attio_ready():
        raise ValueError("Attio credentials not configured. Set ATTIO_API_KEY.")

    results = []
    for p in prospects:
        business_name = p.get("business_name", "")
        try:
            email = (p.get("email") or "").strip()
            if email:
                existing_id = _search_person_by_email(email)
                if existing_id:
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_id,
                        "status": "duplicate_skipped",
                        "email_attempted": False,
                        "email_sent": False,
                        "template_key": "",
                        "template_valid": True,
                        "template_issues": [],
                        "provider": "attio",
                    })
                    continue

                # Marcus path: create Company first, then Person linked to Company
                company_id = None
                if source_agent == "marcus" and business_key == "callingdigital":
                    try:
                        company_id = _create_company_record(p, source_agent, business_key)
                        logging.info("[Attio] Created company for %s: %s", business_name, company_id)
                    except Exception as ce:
                        logging.warning("[Attio] Company creation failed for %s: %s", business_name, ce)

                    # Inject company link into prospect for person creation
                    if company_id:
                        p["_attio_company_id"] = company_id

                rec_id = _create_person_record(p, source_agent, business_key)
                workflow_touched = False
                if source_agent == "marcus" and business_key == "callingdigital":
                    workflow_touched = _touch_person_pipeline_stage(rec_id)

                # Marcus: Attio sequences handle emails — skip direct email sending.
                # The workflow trigger (Record created/updated) auto-enrolls into the
                # vertical-specific sequence. No email composition needed here.
                if source_agent == "marcus" and business_key == "callingdigital":
                    email_attempted = False
                    email_sent = False
                    rendered = {"template_key": "", "valid": True, "issues": []}
                    logging.info(
                        "[Attio] Marcus prospect %s — email handled by Attio sequence (workflow_touched=%s)",
                        business_name, workflow_touched,
                    )
                else:
                    rendered = compose_templated_email(p, business_key=business_key, agent_name=source_agent)
                    mode = email_delivery_mode()
                    if strict_template_validation_enabled() and not rendered.get("valid", False):
                        email_attempted = False
                        email_sent = False
                        logging.warning(
                            "[Attio] Email blocked by template validation for %s: %s",
                            business_name,
                            ",".join(rendered.get("issues", [])),
                        )
                    elif email and already_emailed(email):
                        email_attempted = False
                        email_sent = False
                        logging.info(
                            "[Attio] Email skipped for %s — already emailed %s within 90 days.",
                            business_name, email,
                        )
                    elif mode == "unified":
                        email_attempted = bool((p.get("email") or "").strip() and rendered.get("subject") and rendered.get("body_text"))
                        email_sent = send_unified_email(
                            p.get("email", ""),
                            rendered.get("subject", ""),
                            rendered.get("body_text", ""),
                            business_key=business_key,
                        ) if email_attempted else False
                    else:
                        if rendered.get("subject"):
                            p["subject"] = rendered.get("subject", "")
                        if rendered.get("body_text"):
                            p["body"] = rendered.get("body_text", "")
                        email_attempted = bool((p.get("email") or "").strip() and rendered.get("subject") and rendered.get("body_text") and attio_email_ready())
                        email_sent = _send_email_via_smtp(p) if email_attempted else False
            else:
                existing_id = _search_company_by_name(business_name)
                if existing_id:
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_id,
                        "status": "duplicate_skipped",
                        "email_attempted": False,
                        "email_sent": False,
                        "template_key": "",
                        "template_valid": True,
                        "template_issues": [],
                        "provider": "attio",
                    })
                    continue
                rec_id = _create_company_record(p, source_agent, business_key)
                email_attempted = False
                email_sent = False
                rendered = {"template_key": "", "valid": True, "issues": []}
                workflow_touched = False

            results.append({
                "business_name": business_name,
                "contact_id": rec_id,
                "status": "created",
                "contact_email": email,
                "email_attempted": email_attempted,
                "email_sent": email_sent,
                "template_key": rendered.get("template_key", ""),
                "template_valid": bool(rendered.get("valid", False)),
                "template_issues": rendered.get("issues", []),
                "workflow_touched": workflow_touched,
                "provider": "attio",
            })
        except Exception as e:
            if isinstance(e, ServiceCallError) and getattr(e, "error", None) and e.error.status_code == 400 and email:
                existing_id = _search_person_by_email(email)
                if existing_id:
                    logging.warning("[Attio] Duplicate person detected after 400 for %s — skipping", business_name)
                    results.append({
                        "business_name": business_name,
                        "contact_id": existing_id,
                        "status": "duplicate_skipped",
                        "email_attempted": False,
                        "email_sent": False,
                        "template_key": "",
                        "template_valid": True,
                        "template_issues": [],
                        "provider": "attio",
                    })
                    continue
            logging.error(f"[Attio] Failed to push prospect {business_name}: {e}")
            results.append({
                "business_name": business_name,
                "status": "failed",
                "email_attempted": False,
                "email_sent": False,
                "template_key": "",
                "template_valid": False,
                "template_issues": ["send_path_failed"],
                "provider": "attio",
                "error": str(e),
            })

    return results
