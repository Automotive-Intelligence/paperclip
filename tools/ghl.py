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
import html
import re
from typing import Optional
from services.errors import ServiceCallError
from services.http_client import request_with_retry
from tools.outbound_email import email_delivery_mode, send_unified_email

GHL_BASE_URL = "https://services.leadconnectorhq.com"


def _slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:96].strip("-") or "daily-update"


def _to_html_paragraphs(text: str) -> str:
    blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()]
    if not blocks:
        return ""
    return "".join(f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>" for block in blocks)


def build_ghl_site_graphic_svg(title: str, subtitle: str = "AI Phone Guy") -> str:
    """Create a deterministic branded SVG hero graphic for GHL site posts."""
    safe_title = html.escape((title or "").strip()[:140])
    safe_subtitle = html.escape((subtitle or "").strip()[:90])
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='630' viewBox='0 0 1200 630' role='img' "
        "aria-label='The AI Phone Guy content graphic'>"
        "<defs>"
        "<linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0%' stop-color='#0f172a'/>"
        "<stop offset='100%' stop-color='#1e3a8a'/>"
        "</linearGradient>"
        "</defs>"
        "<rect width='1200' height='630' fill='url(#bg)'/>"
        "<circle cx='1030' cy='120' r='190' fill='#22d3ee' opacity='0.14'/>"
        "<circle cx='120' cy='560' r='180' fill='#38bdf8' opacity='0.12'/>"
        "<text x='88' y='130' fill='#7dd3fc' font-family='Arial, Helvetica, sans-serif' font-size='30' font-weight='700'>"
        "THE AI PHONE GUY"
        "</text>"
        "<text x='88' y='280' fill='#ffffff' font-family='Arial, Helvetica, sans-serif' font-size='62' font-weight='700'>"
        f"{safe_title}"
        "</text>"
        "<text x='88' y='360' fill='#bfdbfe' font-family='Arial, Helvetica, sans-serif' font-size='36' font-weight='500'>"
        f"{safe_subtitle}"
        "</text>"
        "<rect x='88' y='430' width='392' height='72' rx='14' fill='#0ea5e9'/>"
        "<text x='118' y='476' fill='#ffffff' font-family='Arial, Helvetica, sans-serif' font-size='33' font-weight='700'>"
        "Never miss a revenue call"
        "</text>"
        "</svg>"
    )


def ghl_site_publish_ready() -> bool:
    """GHL site publishing requires a webhook URL and core GHL credentials."""
    return bool(
        os.getenv("GHL_API_KEY", "").strip()
        and os.getenv("GHL_LOCATION_ID", "").strip()
        and os.getenv("GHL_SITE_PUBLISH_WEBHOOK_URL", "").strip()
    )


def ghl_social_publish_ready() -> bool:
    """GHL social publishing requires a webhook URL and core GHL credentials."""
    return bool(
        os.getenv("GHL_API_KEY", "").strip()
        and os.getenv("GHL_LOCATION_ID", "").strip()
        and os.getenv("GHL_SOCIAL_PUBLISH_WEBHOOK_URL", "").strip()
    )


def publish_content_to_ghl_site(content_item: dict) -> dict:
    """Publish a queued content item to a GHL site via webhook integration.

    This keeps Paperclip provider-agnostic while allowing GHL-side site workflows
    to handle final post creation.
    """
    webhook_url = os.getenv("GHL_SITE_PUBLISH_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise ValueError("GHL_SITE_PUBLISH_WEBHOOK_URL is not configured.")

    title = (content_item.get("title") or "AI Phone Guy Update").strip()
    body = (content_item.get("body") or "").strip()
    cta = (content_item.get("cta") or "").strip()
    hashtags = (content_item.get("hashtags") or "").strip()
    slug = _slugify(title)
    graphic_svg = build_ghl_site_graphic_svg(title=title, subtitle="DFW AI Receptionist Insights")

    payload = {
        "title": title,
        "slug": slug,
        "body_text": body,
        "body_html": _to_html_paragraphs(body),
        "cta": cta,
        "hashtags": hashtags,
        "platform": content_item.get("platform", ""),
        "content_type": content_item.get("content_type", ""),
        "graphic": {
            "format": "svg",
            "filename": f"{slug}.svg",
            "alt": f"{title} - The AI Phone Guy",
            "svg": graphic_svg,
        },
        "source": {
            "system": "paperclip",
            "business_key": content_item.get("business_key", "aiphoneguy"),
            "queue_id": content_item.get("id"),
            "agent_name": content_item.get("agent_name", "zoe"),
        },
    }

    extra_auth = os.getenv("GHL_SITE_PUBLISH_WEBHOOK_AUTH", "").strip()
    headers = {"Content-Type": "application/json"}
    if extra_auth:
        headers["Authorization"] = extra_auth

    result = request_with_retry(
        provider="ghl",
        operation="publish_content_to_ghl_site",
        method="POST",
        url=webhook_url,
        headers=headers,
        json_body=payload,
        timeout=20,
        max_attempts=3,
        backoff_seconds=0.8,
    )
    if not result.ok:
        err = result.error.message if result.error else "unknown"
        raise RuntimeError(f"GHL site publish failed: {err}")

    data = result.data if isinstance(result.data, dict) else {}
    return {
        "status": "published",
        "slug": slug,
        "url": data.get("url", ""),
        "external_id": data.get("id", ""),
        "provider": "ghl_webhook",
    }


def publish_content_to_ghl_social(content_item: dict) -> dict:
    """Publish a queued social content item to GHL social workflow via webhook."""
    webhook_url = os.getenv("GHL_SOCIAL_PUBLISH_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise ValueError("GHL_SOCIAL_PUBLISH_WEBHOOK_URL is not configured.")

    title = (content_item.get("title") or "AI Phone Guy Social Post").strip()
    body = (content_item.get("body") or "").strip()
    hashtags = (content_item.get("hashtags") or "").strip()
    platform = (content_item.get("platform") or "linkedin").strip().lower()
    post_text = body
    if hashtags:
        post_text = f"{body}\n\n{hashtags}".strip()

    payload = {
        "title": title,
        "platform": platform,
        "post_text": post_text,
        "cta": (content_item.get("cta") or "").strip(),
        "graphic": {
            "format": "svg",
            "filename": f"social-{_slugify(title)}.svg",
            "alt": f"{title} - social graphic",
            "svg": build_ghl_site_graphic_svg(title=title, subtitle="Social Content"),
        },
        "source": {
            "system": "paperclip",
            "business_key": content_item.get("business_key", "aiphoneguy"),
            "queue_id": content_item.get("id"),
            "agent_name": content_item.get("agent_name", "zoe"),
        },
    }

    extra_auth = os.getenv("GHL_SOCIAL_PUBLISH_WEBHOOK_AUTH", "").strip()
    headers = {"Content-Type": "application/json"}
    if extra_auth:
        headers["Authorization"] = extra_auth

    result = request_with_retry(
        provider="ghl",
        operation="publish_content_to_ghl_social",
        method="POST",
        url=webhook_url,
        headers=headers,
        json_body=payload,
        timeout=20,
        max_attempts=3,
        backoff_seconds=0.8,
    )
    if not result.ok:
        err = result.error.message if result.error else "unknown"
        raise RuntimeError(f"GHL social publish failed: {err}")

    data = result.data if isinstance(result.data, dict) else {}
    return {
        "status": "published",
        "platform": platform,
        "url": data.get("url", ""),
        "external_id": data.get("id", ""),
        "provider": "ghl_social_webhook",
    }


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

    name_parts = (business_name or "").strip().split()
    first_name = name_parts[0] if name_parts else "Prospect"
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Lead"

    payload = {
        "locationId": location_id,
        "firstName": first_name,
        "lastName": last_name,
        "name": business_name,
        "companyName": business_name,
        "city": city,
        "source": source_labels.get(source_agent, f"{source_agent} AI Prospecting"),
        "tags": tags,
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
                    "email_attempted": False,
                    "email_sent": False,
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
            email_attempted = False
            email_sent = False
            if contact_id and p.get("subject") and p.get("body"):
                try:
                    mode = email_delivery_mode()
                    if mode == "unified":
                        # Unified mode requires an explicit recipient email.
                        if p.get("email"):
                            email_attempted = True
                            email_sent = send_unified_email(p.get("email", ""), p["subject"], p["body"])
                        else:
                            logging.info(f"[GHL] Unified send skipped for {p.get('business_name')} — missing email.")
                    else:
                        email_attempted = True
                        send_email(
                            contact_id=contact_id,
                            subject=p["subject"],
                            body=p["body"],
                        )
                        email_sent = True
                    if email_sent:
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
            else:
                logging.info(f"[GHL] Email skipped for {p.get('business_name')} — missing subject/body.")

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
                "email_attempted": email_attempted,
                "email_sent": email_sent,
            })

        except Exception as e:
            logging.error(f"[GHL] Failed to push prospect {p.get('business_name')}: {e}")
            results.append({
                "business_name": p.get("business_name"),
                "status": "failed",
                "email_attempted": False,
                "email_sent": False,
                "error": str(e),
            })

    created = len([r for r in results if r["status"] == "created"])
    emails_sent = len([r for r in results if r.get("email_sent")])
    logging.info(f"[GHL] Pushed {created}/{len(prospects)} prospects | {emails_sent} emails sent")
    return results
