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

from typing import Dict, Tuple

from config.runtime import get_settings
from tools.ghl import push_prospects_to_ghl
from tools.hubspot import push_prospects_to_hubspot
from tools.attio import push_prospects_to_attio


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


def push_prospects_to_crm(prospects: list, source_agent: str, business_key: str) -> Tuple[str, list]:
    provider = resolve_provider(business_key=business_key, agent_name=source_agent)
    if provider == "ghl":
        return provider, push_prospects_to_ghl(prospects, source_agent=source_agent, business_key=business_key)
    if provider == "hubspot":
        return provider, push_prospects_to_hubspot(prospects, source_agent=source_agent, business_key=business_key)
    if provider == "attio":
        return provider, push_prospects_to_attio(prospects, source_agent=source_agent, business_key=business_key)
    raise ValueError(f"Unsupported CRM provider '{provider}' for business '{business_key}'")
