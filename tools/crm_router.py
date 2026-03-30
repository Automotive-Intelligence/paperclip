"""
tools/crm_router.py - Provider router for multi-CRM push flows.

Company-level CRM mapping is the default. Agent-level mapping can override.
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

import logging
from typing import Dict, List, Tuple

from config.runtime import get_settings
from services.database import execute_query
from tools.ghl import push_prospects_to_ghl
from tools.hubspot import push_prospects_to_hubspot
from tools.attio import push_prospects_to_attio

logger = logging.getLogger(__name__)


def resolve_provider(business_key: str, agent_name: str) -> str:
    settings = get_settings()
    return settings.resolve_crm_provider(business_key=business_key, agent_id=agent_name)


def provider_ready(provider: str) -> bool:
    settings = get_settings()
    return settings.crm_provider_ready(provider)


def crm_status_snapshot() -> Dict[str, object]:
    settings = get_settings()
    business_mapping = dict(settings.business_crm_map)
    return {
        "business_crm_map": business_mapping,
        "agent_crm_map": dict(settings.agent_crm_map),
        "provider_readiness": {
            "ghl": settings.ghl_ready,
            "hubspot": settings.hubspot_ready,
            "attio": settings.attio_ready,
        },
    }


def log_crm_push(agent_name: str, crm_provider: str, business_name: str, status: str) -> None:
    """Log each CRM push attempt to PostgreSQL for ops reporting."""
    try:
        execute_query(
            "INSERT INTO crm_push_logs (agent_name, crm_provider, business_name, status) "
            "VALUES (%s, %s, %s, %s)",
            (agent_name, crm_provider, (business_name or "")[:255], status),
        )
    except Exception as e:
        logger.error("[CRM] Failed to log push for %s: %s", business_name, e)


def push_prospects_to_crm(prospects: list, source_agent: str, business_key: str) -> Tuple[str, list]:
    provider = resolve_provider(business_key=business_key, agent_name=source_agent)
    if provider == "ghl":
        results = push_prospects_to_ghl(prospects, source_agent=source_agent, business_key=business_key)
    elif provider == "hubspot":
        results = push_prospects_to_hubspot(prospects, source_agent=source_agent, business_key=business_key)
    elif provider == "attio":
        results = push_prospects_to_attio(prospects, source_agent=source_agent, business_key=business_key)
    else:
        raise ValueError(f"Unsupported CRM provider '{provider}' for business '{business_key}'")

    for r in results:
        log_crm_push(source_agent, provider, r.get("business_name", ""), r.get("status", "unknown"))

    return provider, results
