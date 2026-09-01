# services/llm_ledger.py
"""LLM spend ledger — one row per Anthropic API call, with computed USD cost.

This is the source-of-truth for fine-grained AI spend attribution (per-persona,
per-surface, per-brand, per-client) that the Anthropic Console can't give you.
The daily spend email (services/spend_email.py) reads from here.

Design rule: recording spend must NEVER break the caller. Every public function
swallows its own exceptions and logs — a ledger failure must not fail a ship.

Wire it in right after an Anthropic call:

    resp = client.messages.create(...)
    from services.llm_ledger import record_from_response
    record_from_response(resp, persona="CMO", surface="executor")
"""

import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

# Per-million-token pricing (USD), input / output. Matched by substring against
# the model id so version bumps (opus-4-7 -> opus-4-8) don't need a code change.
# Source: Anthropic pricing as of 2026-06. Update here when prices change.
_PRICING = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
# Cache multipliers applied to the INPUT rate.
_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10

# If a model id matches nothing, price it at the most expensive tier so spend is
# never silently under-reported, and log once so we add the row to _PRICING.
_DEFAULT_RATES = _PRICING["opus"]
_warned_models: set = set()

_TABLE_READY = False


def _rates_for(model: str) -> tuple:
    m = (model or "").lower()
    for key, rates in _PRICING.items():
        if key in m:
            return rates
    if model not in _warned_models:
        logger.warning("[llm_ledger] no pricing for model %r — using opus rates", model)
        _warned_models.add(model)
    return _DEFAULT_RATES


def _cost_usd(model: str, inp: int, out: int, cache_write: int, cache_read: int) -> float:
    in_rate, out_rate = _rates_for(model)
    return (
        inp * in_rate
        + out * out_rate
        + cache_write * in_rate * _CACHE_WRITE_MULT
        + cache_read * in_rate * _CACHE_READ_MULT
    ) / 1_000_000.0


def _ensure_table() -> None:
    """Lazy-create the ledger table (idempotent). Mirrors ape_routine_digest_queue."""
    global _TABLE_READY
    if _TABLE_READY:
        return
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS llm_spend_ledger (
            id                    BIGSERIAL PRIMARY KEY,
            ts                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            persona               TEXT,
            surface               TEXT,
            brand                 TEXT,
            client                TEXT,
            model                 TEXT NOT NULL,
            input_tokens          BIGINT NOT NULL DEFAULT 0,
            output_tokens         BIGINT NOT NULL DEFAULT 0,
            cache_creation_tokens BIGINT NOT NULL DEFAULT 0,
            cache_read_tokens     BIGINT NOT NULL DEFAULT 0,
            cost_usd              NUMERIC(12, 6) NOT NULL DEFAULT 0,
            request_id            TEXT
        );
        """
    )
    # Indexes are best-effort; ignore if the DDL above already ran them elsewhere.
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_llm_spend_ts ON llm_spend_ledger(ts)",
        "CREATE INDEX IF NOT EXISTS ix_llm_spend_persona ON llm_spend_ledger(persona, ts)",
    ):
        try:
            execute_query(ddl)
        except Exception:  # pragma: no cover - index race is non-fatal
            pass
    _TABLE_READY = True


def record_usage(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    *,
    persona: Optional[str] = None,
    surface: Optional[str] = None,
    brand: Optional[str] = None,
    client: Optional[str] = None,
    request_id: Optional[str] = None,
    cost_usd_override: Optional[float] = None,
) -> Optional[float]:
    """Insert one ledger row. Returns computed cost_usd, or None on failure.

    Pass cost_usd_override when the caller already has an authoritative cost
    (e.g. LiteLLM's own per-model cost for Gemini/DeepSeek, which our Anthropic
    pricing map doesn't cover). Otherwise cost is computed from the pricing map.

    Never raises — a ledger failure must not break the calling agent.
    """
    try:
        cost = (
            cost_usd_override
            if cost_usd_override is not None
            else _cost_usd(
                model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
            )
        )
        _ensure_table()
        execute_query(
            """
            INSERT INTO llm_spend_ledger
              (persona, surface, brand, client, model, input_tokens, output_tokens,
               cache_creation_tokens, cache_read_tokens, cost_usd, request_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                persona, surface, brand, client, model,
                int(input_tokens or 0), int(output_tokens or 0),
                int(cache_creation_tokens or 0), int(cache_read_tokens or 0),
                round(cost, 6), request_id,
            ),
        )
        return cost
    except Exception as e:
        logger.warning("[llm_ledger] record_usage failed (non-fatal): %s", e)
        return None


def record_from_response(
    response: Any,
    *,
    persona: Optional[str] = None,
    surface: Optional[str] = None,
    brand: Optional[str] = None,
    client: Optional[str] = None,
) -> Optional[float]:
    """Pull model + usage off an Anthropic Message response and record a row.

    Defensive about the response shape so a usage-field rename upstream degrades
    to a logged warning, not a crash.
    """
    try:
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", None) or "unknown"
        return record_usage(
            model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            persona=persona,
            surface=surface,
            brand=brand,
            client=client,
            request_id=getattr(response, "_request_id", None),
        )
    except Exception as e:
        logger.warning("[llm_ledger] record_from_response failed (non-fatal): %s", e)
        return None


def daily_totals(day: Optional[date] = None) -> Dict[str, Any]:
    """Spend rollup for a single UTC day (defaults to today).

    Returns {total_usd, calls, by_persona: [...], by_model: [...], by_client: [...]}.
    """
    day = day or datetime.now(timezone.utc).date()
    out: Dict[str, Any] = {
        "day": day.isoformat(),
        "total_usd": 0.0,
        "calls": 0,
        "by_persona": [],
        "by_model": [],
        "by_client": [],
    }
    try:
        total = fetch_all(
            "SELECT COALESCE(SUM(cost_usd),0), COUNT(*) FROM llm_spend_ledger "
            "WHERE ts::date = %s",
            (day,),
        )
        out["total_usd"] = float(total[0][0]) if total else 0.0
        out["calls"] = int(total[0][1]) if total else 0

        out["by_persona"] = [
            {"persona": r[0] or "(untagged)", "cost_usd": float(r[1]), "calls": int(r[2])}
            for r in fetch_all(
                "SELECT persona, SUM(cost_usd), COUNT(*) FROM llm_spend_ledger "
                "WHERE ts::date = %s GROUP BY persona ORDER BY SUM(cost_usd) DESC",
                (day,),
            )
        ]
        out["by_model"] = [
            {"model": r[0], "cost_usd": float(r[1]), "calls": int(r[2])}
            for r in fetch_all(
                "SELECT model, SUM(cost_usd), COUNT(*) FROM llm_spend_ledger "
                "WHERE ts::date = %s GROUP BY model ORDER BY SUM(cost_usd) DESC",
                (day,),
            )
        ]
        out["by_client"] = [
            {"client": r[0], "cost_usd": float(r[1]), "calls": int(r[2])}
            for r in fetch_all(
                "SELECT client, SUM(cost_usd), COUNT(*) FROM llm_spend_ledger "
                "WHERE ts::date = %s AND client IS NOT NULL "
                "GROUP BY client ORDER BY SUM(cost_usd) DESC",
                (day,),
            )
        ]
    except Exception as e:
        logger.warning("[llm_ledger] daily_totals failed: %s", e)
    return out


# ---------------------------------------------------------------------------
# 2026-08-30: metering truth. The ledger was built (#88) to capture spend at
# the SDK/LiteLLM call site, but every real spender since then (Slipstream ->
# OpenRouter, Studio social -> Anthropic Messages, fal images) calls its
# provider with raw requests and never touched this table -- so the daily
# email read "$0.00, 0 calls" for weeks against real spend. Two fixes live
# here: a recorder for OpenAI-style (OpenRouter) responses so the direct call
# sites can write rows, and provider-level ground truth (OpenRouter's own
# cumulative usage counter, snapshotted daily) that is correct no matter which
# code path spent the money.
# ---------------------------------------------------------------------------


def record_openrouter_response(
    j: Any,
    *,
    model: str,
    persona: Optional[str] = None,
    surface: Optional[str] = None,
    brand: Optional[str] = None,
) -> Optional[float]:
    """Record one OpenAI-style chat completion (OpenRouter) response. Uses the
    provider's own `usage.cost` when present (request with usage.include=true),
    else prices prompt/completion tokens via the map. Never raises."""
    try:
        usage = (j or {}).get("usage") or {}
        cost = usage.get("cost")
        return record_usage(
            (j or {}).get("model") or model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            persona=persona, surface=surface, brand=brand,
            request_id=(j or {}).get("id"),
            cost_usd_override=float(cost) if cost is not None else None,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[llm_ledger] openrouter record failed: %s", e)
        return None


_SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provider_spend_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider     TEXT NOT NULL,
    total_usage  NUMERIC(14, 6) NOT NULL,
    balance      NUMERIC(14, 6)
);
"""


def openrouter_usage() -> Optional[Dict[str, float]]:
    """{'total_usage': cumulative USD spent, 'balance': remaining USD} from
    OpenRouter's credits endpoint, or None if unreadable. Provider ground truth:
    counts every OpenRouter call regardless of which code path made it."""
    import os
    import requests
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return None
    try:
        r = requests.get("https://openrouter.ai/api/v1/credits",
                         headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if not r.ok:
            return None
        d = (r.json() or {}).get("data") or {}
        total, used = float(d["total_credits"]), float(d["total_usage"])
        return {"total_usage": used, "balance": total - used}
    except Exception as e:
        logger.warning("[llm_ledger] openrouter usage fetch failed: %s", e)
        return None


def snapshot_provider(provider: str, total_usage: float, balance: Optional[float]) -> None:
    """Append today's cumulative-usage reading. Never raises."""
    try:
        execute_query(_SNAPSHOT_TABLE_SQL)
        execute_query(
            "INSERT INTO provider_spend_snapshots (provider, total_usage, balance) "
            "VALUES (%s, %s, %s)", (provider, total_usage, balance))
    except Exception as e:
        logger.warning("[llm_ledger] snapshot write failed: %s", e)


def provider_delta(provider: str, current_total: float, min_age_hours: float = 20) -> Optional[Dict[str, Any]]:
    """USD spent since the newest snapshot at least `min_age_hours` old (so a
    same-morning re-run does not report a near-zero delta). None until a
    baseline exists."""
    try:
        rows = fetch_all(
            "SELECT total_usage, ts FROM provider_spend_snapshots WHERE provider = %s "
            "AND ts <= NOW() - (%s * INTERVAL '1 hour') ORDER BY ts DESC LIMIT 1",
            (provider, min_age_hours))
    except Exception as e:
        logger.warning("[llm_ledger] snapshot read failed: %s", e)
        return None
    if not rows:
        return None
    prior, ts = float(rows[0][0]), rows[0][1]
    return {"spent_usd": max(0.0, current_total - prior), "since": ts}


def tavily_usage() -> Optional[Dict[str, Any]]:
    """Tavily search-credit truth from api.tavily.com/usage: plan usage/limit,
    paygo usage, and whether paygo is CAPPED. Discovered 2026-08-31: August
    closed at 4,920/4,000 credits and paygo (enabled, uncapped) silently billed
    its first $7.76 overage -- nothing metered it. None if unreadable."""
    import os
    import requests
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return None
    try:
        r = requests.get("https://api.tavily.com/usage",
                         headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if not r.ok:
            return None
        acct = (r.json() or {}).get("account") or {}
        return {
            "plan_usage": int(acct.get("plan_usage") or 0),
            "plan_limit": int(acct.get("plan_limit") or 0),
            "paygo_usage": int(acct.get("paygo_usage") or 0),
            "paygo_limit": acct.get("paygo_limit"),  # None = UNCAPPED billing
        }
    except Exception as e:
        logger.warning("[llm_ledger] tavily usage fetch failed: %s", e)
        return None


def zernio_spend() -> Optional[Dict[str, Any]]:
    """Zernio billing truth from zernio.com/api/v1/usage. Decoded 2026-08-31:
    the 07-29 usage-based migration bills ~$0.01 per connected account per HOUR
    (~$7.40/account/month) -- August closed at $92.20 for ~13 mostly-idle
    accounts vs ~$15/mo on the old plan. None if unreadable."""
    import os
    import requests
    key = (os.environ.get("ZERNIO_API_KEY") or "").strip()
    if not key:
        return None
    try:
        r = requests.get("https://zernio.com/api/v1/usage",
                         headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if not r.ok:
            return None
        d = r.json() or {}
        spend = d.get("spend") or {}
        usage = d.get("usage") or {}
        return {
            "plan": d.get("planName"),
            "period_usd": float(spend.get("currentPeriodCents") or 0) / 100.0,
            "x_usd": float(spend.get("xSpendCents") or 0) / 100.0,
            "connected_accounts": usage.get("connectedAccounts"),
        }
    except Exception as e:
        logger.warning("[llm_ledger] zernio usage fetch failed: %s", e)
        return None
