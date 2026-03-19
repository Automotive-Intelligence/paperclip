"""
config/runtime.py - Central runtime settings and startup validation.

This keeps environment parsing in one place so app startup and tools all use the
same rules for credentials, strictness, and provider routing.
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
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple


def _parse_csv(raw: str) -> Tuple[str, ...]:
    if not raw:
        return tuple()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _parse_json_map(raw: str, default: Dict[str, str]) -> Dict[str, str]:
    if not raw:
        return dict(default)
    try:
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            return dict(default)
        parsed: Dict[str, str] = {}
        for k, v in loaded.items():
            key = str(k).strip().lower()
            value = str(v).strip().lower()
            if key and value:
                parsed[key] = value
        return parsed or dict(default)
    except Exception:
        return dict(default)


def resolve_llm_model_and_key() -> Tuple[str, Optional[str]]:
    """Resolve model and API key using one provider-aware strategy."""
    model = os.getenv("LLM_MODEL") or os.getenv("GROQ_MODEL") or "groq/llama-3.1-8b-instant"
    api_key = os.getenv("LLM_API_KEY")

    if not api_key:
        if model.startswith("groq/"):
            api_key = os.getenv("GROQ_API_KEY")
        elif model.startswith("openrouter/"):
            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        elif model.startswith("deepseek/"):
            api_key = os.getenv("DEEPSEEK_API_KEY")
        else:
            api_key = os.getenv("OPENAI_API_KEY")

    return model, api_key


@dataclass(frozen=True)
class RuntimeSettings:
    environment: str
    strict_startup: bool
    timezone: str
    app_version: str
    api_keys: Tuple[str, ...]
    database_url: str
    llm_model: str
    llm_api_key_present: bool
    business_crm_map: Dict[str, str]
    agent_crm_map: Dict[str, str]
    ghl_api_key_present: bool
    ghl_location_id_present: bool
    hubspot_api_key_present: bool
    attio_api_key_present: bool

    @property
    def postgres_enabled(self) -> bool:
        return bool(self.database_url)

    @property
    def ghl_ready(self) -> bool:
        return self.ghl_api_key_present and self.ghl_location_id_present

    @property
    def llm_ready(self) -> bool:
        return self.llm_api_key_present

    @property
    def hubspot_ready(self) -> bool:
        return self.hubspot_api_key_present

    @property
    def attio_ready(self) -> bool:
        return self.attio_api_key_present

    def crm_provider_ready(self, provider: str) -> bool:
        p = (provider or "").strip().lower()
        if p == "ghl":
            return self.ghl_ready
        if p == "hubspot":
            return self.hubspot_ready
        if p == "attio":
            return self.attio_ready
        return False

    def resolve_crm_provider(self, business_key: str, agent_id: Optional[str] = None) -> str:
        agent_key = (agent_id or "").strip().lower()
        if agent_key and agent_key in self.agent_crm_map:
            return self.agent_crm_map[agent_key]
        biz_key = (business_key or "").strip().lower()
        return self.business_crm_map.get(biz_key, "ghl")

    def startup_warnings(self) -> List[str]:
        warnings: List[str] = []
        if not self.api_keys:
            warnings.append("API_KEYS not set: auth is open for protected endpoints.")
        if not self.postgres_enabled:
            warnings.append("DATABASE_URL not set: Postgres-backed persistence is disabled.")
        if not self.llm_ready:
            warnings.append("No LLM API key resolved for configured model.")
        if not self.ghl_ready:
            warnings.append("GHL not fully configured: sales push to CRM is disabled.")
        for business, provider in self.business_crm_map.items():
            if not self.crm_provider_ready(provider):
                warnings.append(
                    f"CRM provider '{provider}' is mapped to '{business}' but missing credentials."
                )
        return warnings

    def startup_fatals(self) -> List[str]:
        """
        Fatal checks are intentionally minimal and only applied when strict_startup is enabled.
        """
        fatals: List[str] = []
        if self.strict_startup and not self.llm_ready:
            fatals.append("STRICT_STARTUP is enabled but LLM credentials are missing.")
        if self.strict_startup:
            for business, provider in self.business_crm_map.items():
                if not self.crm_provider_ready(provider):
                    fatals.append(
                        f"STRICT_STARTUP is enabled but CRM provider '{provider}' for '{business}' is not configured."
                    )
        return fatals


@lru_cache(maxsize=1)
def get_settings() -> RuntimeSettings:
    model, api_key = resolve_llm_model_and_key()
    default_business_crm_map = {
        "aiphoneguy": "ghl",
        "callingdigital": "attio",
        "autointelligence": "hubspot",
    }
    business_crm_map = _parse_json_map(os.getenv("BUSINESS_CRM_MAP", ""), default_business_crm_map)
    agent_crm_map = _parse_json_map(os.getenv("AGENT_CRM_MAP", ""), {})
    return RuntimeSettings(
        environment=(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower(),
        strict_startup=(os.getenv("STRICT_STARTUP", "false").strip().lower() in {"1", "true", "yes", "on"}),
        timezone=(os.getenv("APP_TIMEZONE") or "America/Chicago").strip(),
        app_version=(os.getenv("APP_VERSION") or "4.0.0").strip(),
        api_keys=_parse_csv(os.getenv("API_KEYS", "")),
        database_url=_normalize_database_url((os.getenv("DATABASE_URL") or "").strip()),
        llm_model=model,
        llm_api_key_present=bool(api_key),
        business_crm_map=business_crm_map,
        agent_crm_map=agent_crm_map,
        ghl_api_key_present=bool((os.getenv("GHL_API_KEY") or "").strip()),
        ghl_location_id_present=bool((os.getenv("GHL_LOCATION_ID") or "").strip()),
        hubspot_api_key_present=bool((os.getenv("HUBSPOT_API_KEY") or os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()),
        attio_api_key_present=bool((os.getenv("ATTIO_API_KEY") or "").strip()),
    )