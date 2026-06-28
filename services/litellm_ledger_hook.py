# services/litellm_ledger_hook.py
"""One global LiteLLM callback that records every worker-agent completion into
llm_spend_ledger — so the ledger (and the daily spend email) covers the ~22
LiteLLM/CrewAI agents, not just the Anthropic-SDK persona layer.

Worker agents route through LiteLLM (CrewAI's LLM(provider="litellm") and direct
litellm.completion calls in tools/). A single module-global success callback
captures all of them at once, instead of editing every call site.

Cost: we prefer LiteLLM's own per-model cost (it knows Gemini/DeepSeek/etc.
prices, which our Anthropic-only pricing map doesn't). Rows land with
surface="agent". Per-agent attribution within the worker bucket needs metadata
threading (agent/river) into the CrewAI calls — a follow-up; today these rows
are distinguishable by model (gemini/deepseek vs the personas' opus).

register() is idempotent and never raises — instrumentation must not break runs.
"""

import logging

logger = logging.getLogger(__name__)

_REGISTERED = False


def register() -> bool:
    """Attach the ledger callback to LiteLLM. Idempotent; safe to call anywhere."""
    global _REGISTERED
    if _REGISTERED:
        return True
    try:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger
    except Exception as e:
        logger.warning("[litellm_ledger] litellm unavailable — worker spend not tracked: %s", e)
        return False

    from services.llm_ledger import record_usage

    def _record(kwargs, response_obj):
        try:
            model = kwargs.get("model") or getattr(response_obj, "model", None) or "unknown"
            usage = getattr(response_obj, "usage", None)
            inp = int(getattr(usage, "prompt_tokens", 0) or 0)
            out = int(getattr(usage, "completion_tokens", 0) or 0)

            # Prefer LiteLLM's own cost (covers Gemini/DeepSeek/OpenRouter).
            cost = kwargs.get("response_cost")
            if cost is None:
                try:
                    cost = litellm.completion_cost(completion_response=response_obj)
                except Exception:
                    cost = None  # record_usage will fall back to the pricing map

            meta = (kwargs.get("litellm_params") or {}).get("metadata") or {}
            agent = meta.get("agent") or meta.get("agent_name")
            river = meta.get("river")

            record_usage(
                model,
                input_tokens=inp,
                output_tokens=out,
                persona=agent,        # usually None today; set when CrewAI metadata threads it
                brand=river,
                surface="agent",
                cost_usd_override=cost,
            )
        except Exception as e:  # never let a callback break a completion
            logger.warning("[litellm_ledger] record failed (non-fatal): %s", e)

    class _LedgerLogger(CustomLogger):
        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            _record(kwargs, response_obj)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            _record(kwargs, response_obj)

    try:
        litellm.callbacks = list(litellm.callbacks or []) + [_LedgerLogger()]
    except Exception as e:
        logger.warning("[litellm_ledger] could not attach callback: %s", e)
        return False

    _REGISTERED = True
    logger.info("[litellm_ledger] registered LiteLLM success callback -> llm_spend_ledger")
    return True
