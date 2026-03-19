"""
tools/ghl.py — GoHighLevel CRM Integration for Paperclip
Full revenue pipeline: contact creation, email sending, opportunity management,
workflow triggers, and pipeline tracking across all 3 businesses.
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
from typing import Optional
from services.errors import ServiceCallError
from services.http_client import request_with_retry

GHL_BASE_URL = "https://services.leadconnectorhq.com"


def _get_headers() -> dict:
    """Fresh headers on each call in case env vars load after module import."""
    return {
        "Authorization": f"Bearer {os.getenv('GHL_API_KEY', '')}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }


def _ghl_request(
    operation: str,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: int = 15,
) -> dict:
    response = request_with_retry(
        provider="ghl",
        operation=operation,
        method=method,
        url=f"{GHL_BASE_URL}{path}",
        headers=_get_headers(),
        params=params,
        json_body=json_body,
        timeout=timeout,
        max_attempts=3,
        backoff_seconds=0.7,
    )
    if response.ok:
        return response.data or {}
    if response.error is None:
        raise RuntimeError(f"ghl.{operation} failed with unknown error")
    raise ServiceCallError(response.error)


# ── Contact Management ───────────────────────────────────────────────────────


def create_contact(
    business_name: str,
    city: str,
    business_type: str,
    email_hook: str,
    reason: str,
    source_agent: str = "tyler",
    tags: Optional[list] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    business_key: str = "aiphoneguy",
) -> dict:
    """
    Create a contact in GHL from any sales agent's prospect.
    Returns the created contact dict or raises on failure.
    """
    location_id = os.getenv("GHL_LOCATION_ID", "")
    if not location_id:
        raise ValueError("GHL_LOCATION_ID not set in environment variables.")

    if tags is None:
        tags = [
            f"{source_agent}-prospect",
            business_key,
            "cold-email",
            business_type.lower().replace(" ", "-"),
        ]

    # Map source agents to readable source labels
    source_labels = {
        "tyler": "Tyler AI Prospecting - Cold Email",
        "marcus": "Marcus AI Prospecting - Consultative",
        "ryan_data": "Ryan Data AI Prospecting - Dealer Outreach",
    }

    payload = {
        "locationId": location_id,
        "name": business_name,
        "companyName": business_name,
        "city": city,
        "source": source_labels.get(source_agent, f"{source_agent} AI Prospecting"),
        "tags": tags,
        "customFields": [
            {"key": "business_type", "value": business_type},
            {"key": "outreach_reason", "value": reason},
            {"key": "email_hook", "value": email_hook},
            {"key": "source_agent", "value": source_agent},
            {"key": "outreach_channel", "value": "cold-email"},
            {"key": "pipeline_stage", "value": "new_prospect"},
        ],
    }

    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone

    data = _ghl_request("create_contact", "POST", "/contacts/", json_body=payload, timeout=15)
    contact = data.get("contact", {})
    logging.info(f"[GHL] Created contact: {business_name} ({city}) — ID: {contact.get('id')}")
    return contact


def search_contact(email: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
    """Search for an existing contact by email or name to avoid duplicates."""
    location_id = os.getenv("GHL_LOCATION_ID", "")
    if not location_id:
        return None

    params = {"locationId": location_id}
    if email:
        params["email"] = email
    elif name:
        params["query"] = name

    try:
        data = _ghl_request("search_contact", "GET", "/contacts/", params=params, timeout=15)
        contacts = data.get("contacts", [])
        return contacts[0] if contacts else None
    except Exception as e:
        logging.warning(f"[GHL] Contact search failed: {e}")
        return None


def update_contact_tags(contact_id: str, tags: list) -> dict:
    """Add tags to an existing contact."""
    return _ghl_request(
        "update_contact_tags",
        "PUT",
        f"/contacts/{contact_id}",
        json_body={"tags": tags},
        timeout=15,
    )


def add_contact_note(contact_id: str, note: str) -> dict:
    """Add a note to an existing GHL contact."""
    location_id = os.getenv("GHL_LOCATION_ID", "")
    return _ghl_request(
        "add_contact_note",
        "POST",
        f"/contacts/{contact_id}/notes",
        json_body={"body": note, "locationId": location_id},
        timeout=15,
    )


# ── Email Sending ────────────────────────────────────────────────────────────


def send_email(
    contact_id: str,
    subject: str,
    body: str,
    from_name: Optional[str] = None,
    from_email: Optional[str] = None,
) -> dict:
    """
    Send a cold email to a GHL contact via the conversations/messages endpoint.
    This uses GHL's built-in email sending (connected mailbox required in GHL).

    Returns the message response or raises on failure.
    """
    payload = {
        "type": "Email",
        "contactId": contact_id,
        "subject": subject,
        "html": body.replace("\n", "<br>"),
        "emailMessageType": "html",
    }

    if from_name:
        payload["emailFrom"] = from_name
    if from_email:
        payload["emailFrom"] = from_email

    result = _ghl_request(
        "send_email",
        "POST",
        "/conversations/messages",
        json_body=payload,
        timeout=15,
    )
    logging.info(f"[GHL] Email sent to contact {contact_id}: '{subject}'")
    return result


def send_email_sequence(
    contact_id: str,
    emails: list,
) -> list:
    """
    Send the first email in a sequence immediately.
    Remaining emails are stored as scheduled follow-ups via GHL workflow.

    emails: list of dicts with keys: subject, body, delay_days (0 for immediate)
    """
    results = []
    for i, email in enumerate(emails):
        if i == 0:
            # Send first touch immediately
            try:
                result = send_email(
                    contact_id=contact_id,
                    subject=email["subject"],
                    body=email["body"],
                )
                results.append({"touch": i + 1, "status": "sent", "result": result})
            except Exception as e:
                logging.error(f"[GHL] Email send failed for touch {i+1}: {e}")
                results.append({"touch": i + 1, "status": "failed", "error": str(e)})
        else:
            # Schedule follow-ups by adding to workflow
            try:
                add_contact_note(
                    contact_id,
                    f"SCHEDULED FOLLOW-UP (Touch {i+1}, Day {email.get('delay_days', i*3)}):\n"
                    f"Subject: {email['subject']}\n\n{email['body']}",
                )
                results.append({"touch": i + 1, "status": "scheduled"})
            except Exception as e:
                results.append({"touch": i + 1, "status": "failed", "error": str(e)})

    return results


# ── Pipeline / Opportunity Management ────────────────────────────────────────


def create_opportunity(
    contact_id: str,
    name: str,
    pipeline_id: str,
    stage_id: str,
    monetary_value: float = 0,
    source_agent: str = "tyler",
) -> dict:
    """
    Create a pipeline opportunity in GHL linked to a contact.
    This tracks revenue in the pipeline.
    """
    location_id = os.getenv("GHL_LOCATION_ID", "")

    payload = {
        "pipelineId": pipeline_id,
        "locationId": location_id,
        "contactId": contact_id,
        "name": name,
        "pipelineStageId": stage_id,
        "status": "open",
        "monetaryValue": monetary_value,
        "source": f"{source_agent} AI Prospecting",
    }

    opp = _ghl_request("create_opportunity", "POST", "/opportunities/", json_body=payload, timeout=15)
    logging.info(f"[GHL] Created opportunity: {name} — ${monetary_value}")
    return opp


def update_opportunity_stage(opportunity_id: str, stage_id: str) -> dict:
    """Move an opportunity to a different pipeline stage."""
    return _ghl_request(
        "update_opportunity_stage",
        "PUT",
        f"/opportunities/{opportunity_id}",
        json_body={"pipelineStageId": stage_id},
        timeout=15,
    )


def get_pipeline_opportunities(pipeline_id: str) -> list:
    """Get all opportunities in a pipeline for revenue tracking."""
    location_id = os.getenv("GHL_LOCATION_ID", "")
    try:
        data = _ghl_request(
            "get_pipeline_opportunities",
            "GET",
            "/opportunities/search",
            params={"location_id": location_id, "pipeline_id": pipeline_id},
            timeout=15,
        )
        return data.get("opportunities", [])
    except Exception as e:
        logging.error(f"[GHL] Pipeline fetch failed: {e}")
        return []


# ── Workflow Triggers ────────────────────────────────────────────────────────


def add_to_workflow(contact_id: str, workflow_id: str) -> dict:
    """Add a contact to a GHL workflow (automation sequence)."""
    result = _ghl_request(
        "add_to_workflow",
        "POST",
        f"/contacts/{contact_id}/workflow/{workflow_id}",
        json_body={},
        timeout=15,
    )
    logging.info(f"[GHL] Contact {contact_id} added to workflow {workflow_id}")
    return result


def remove_from_workflow(contact_id: str, workflow_id: str) -> dict:
    """Remove a contact from a GHL workflow."""
    return _ghl_request(
        "remove_from_workflow",
        "DELETE",
        f"/contacts/{contact_id}/workflow/{workflow_id}",
        timeout=15,
    )


# ── Master Push Functions (per business) ─────────────────────────────────────


def push_prospects_to_ghl(prospects: list, source_agent: str = "tyler", business_key: str = "aiphoneguy") -> list:
    """
    Master function — takes any sales agent's parsed prospect list and pushes all to GHL.
    Creates contacts, sends first-touch cold emails, and creates pipeline opportunities.

    Each prospect dict should have:
        business_name, city, business_type, reason, email_hook
        Optional: email, phone, subject, body, follow_up_subject, follow_up_body, monetary_value
    """
    results = []
    pipeline_id = os.getenv("GHL_PIPELINE_ID", "")
    stage_id = os.getenv("GHL_STAGE_NEW_PROSPECT", "")

    # Monetary values by business
    deal_values = {
        "tyler": 482,       # AI Phone Guy standard rate
        "marcus": 2500,     # Calling Digital retainer
        "ryan_data": 2500,  # Automotive Intelligence audit
    }

    for p in prospects:
        try:
            # Check for duplicate before creating
            existing = search_contact(
                email=p.get("email"),
                name=p.get("business_name"),
            )
            if existing:
                logging.info(f"[GHL] Skipping duplicate: {p.get('business_name')}")
                results.append({
                    "business_name": p.get("business_name"),
                    "contact_id": existing.get("id"),
                    "status": "duplicate_skipped",
                })
                continue

            contact = create_contact(
                business_name=p.get("business_name", "Unknown"),
                city=p.get("city", ""),
                business_type=p.get("business_type", ""),
                email_hook=p.get("email_hook", ""),
                reason=p.get("reason", ""),
                source_agent=source_agent,
                email=p.get("email"),
                phone=p.get("phone"),
                business_key=business_key,
            )
            contact_id = contact.get("id")

            # Add detailed note with outreach context
            hook = p.get("email_hook", "")
            if contact_id and hook:
                add_contact_note(
                    contact_id,
                    f"{source_agent.title()}'s Cold Email Hook:\n{hook}\n\n"
                    f"Targeting Reason:\n{p.get('reason', '')}\n\n"
                    f"Channel: Cold Email (no SMS - no opt-in consent)",
                )

            # Send first-touch cold email if subject and body are available
            email_sent = False
            if contact_id and p.get("subject") and p.get("body"):
                try:
                    send_email(
                        contact_id=contact_id,
                        subject=p["subject"],
                        body=p["body"],
                    )
                    email_sent = True
                    logging.info(f"[GHL] First-touch email sent to {p.get('business_name')}")

                    # Schedule follow-up if available
                    if p.get("follow_up_subject") and p.get("follow_up_body"):
                        add_contact_note(
                            contact_id,
                            f"SCHEDULED FOLLOW-UP (Touch 2, Day 3):\n"
                            f"Subject: {p['follow_up_subject']}\n\n{p['follow_up_body']}",
                        )
                except Exception as email_err:
                    logging.warning(f"[GHL] Email send failed for {p.get('business_name')}: {email_err}")

            # Create pipeline opportunity for revenue tracking
            if contact_id and pipeline_id and stage_id:
                try:
                    create_opportunity(
                        contact_id=contact_id,
                        name=f"{p.get('business_name', 'Unknown')} - {source_agent.title()} Outreach",
                        pipeline_id=pipeline_id,
                        stage_id=stage_id,
                        monetary_value=deal_values.get(source_agent, 0),
                        source_agent=source_agent,
                    )
                except Exception as opp_err:
                    logging.warning(f"[GHL] Opportunity creation failed: {opp_err}")

            # Add to cold email workflow if configured
            workflow_id = os.getenv(f"GHL_WORKFLOW_{source_agent.upper()}", "")
            if contact_id and workflow_id:
                try:
                    add_to_workflow(contact_id, workflow_id)
                except Exception as wf_err:
                    logging.warning(f"[GHL] Workflow add failed: {wf_err}")

            results.append({
                "business_name": p.get("business_name"),
                "contact_id": contact_id,
                "status": "created",
                "email_sent": email_sent,
            })

        except Exception as e:
            logging.error(f"[GHL] Failed to push prospect {p.get('business_name')}: {e}")
            results.append({
                "business_name": p.get("business_name"),
                "status": "failed",
                "error": str(e),
            })

    created = len([r for r in results if r["status"] == "created"])
    emails_sent = len([r for r in results if r.get("email_sent")])
    logging.info(f"[GHL] Pushed {created}/{len(prospects)} prospects | {emails_sent} emails sent")
    return results
