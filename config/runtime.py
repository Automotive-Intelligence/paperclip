"""
config/runtime.py - Central runtime settings and startup validation.

This keeps environment parsing in one place so app startup and tools all use the
same rules for credentials, strictness, and provider routing.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple


def _parse_csv(raw: str) -> Tuple[str, ...]:
    if not raw:
        return tuple()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


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
    ghl_api_key_present: bool
    ghl_location_id_present: bool

    @property
    def postgres_enabled(self) -> bool:
        return bool(self.database_url)

    @property
    def ghl_ready(self) -> bool:
        return self.ghl_api_key_present and self.ghl_location_id_present

    @property
    def llm_ready(self) -> bool:
        return self.llm_api_key_present

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
        return warnings

    def startup_fatals(self) -> List[str]:
        """
        Fatal checks are intentionally minimal and only applied when strict_startup is enabled.
        """
        fatals: List[str] = []
        if self.strict_startup and not self.llm_ready:
            fatals.append("STRICT_STARTUP is enabled but LLM credentials are missing.")
        return fatals


@lru_cache(maxsize=1)
def get_settings() -> RuntimeSettings:
    model, api_key = resolve_llm_model_and_key()
    return RuntimeSettings(
        environment=(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower(),
        strict_startup=(os.getenv("STRICT_STARTUP", "false").strip().lower() in {"1", "true", "yes", "on"}),
        timezone=(os.getenv("APP_TIMEZONE") or "America/Chicago").strip(),
        app_version=(os.getenv("APP_VERSION") or "4.0.0").strip(),
        api_keys=_parse_csv(os.getenv("API_KEYS", "")),
        database_url=_normalize_database_url((os.getenv("DATABASE_URL") or "").strip()),
        llm_model=model,
        llm_api_key_present=bool(api_key),
        ghl_api_key_present=bool((os.getenv("GHL_API_KEY") or "").strip()),
        ghl_location_id_present=bool((os.getenv("GHL_LOCATION_ID") or "").strip()),
    )