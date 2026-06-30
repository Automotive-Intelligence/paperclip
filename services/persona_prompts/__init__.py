"""Persona system prompts for the Autonomous Persona Executor (APE).

Each persona has:
  - A markdown system prompt (e.g. infrastructure.md) loaded as the
    Claude SDK system parameter for ephemeral executor sessions
  - A JSON tool allowlist (e.g. infrastructure_tools.json) declaring
    which tool names the executor is allowed to call

The executor reads both at session-spawn time and never includes any
tool not in the allowlist in the SDK tool definitions it passes.
"""

from pathlib import Path

from config.principles import foundation_header, foundation_bible_header

_PROMPTS_DIR = Path(__file__).parent


def load_persona_prompt(persona: str) -> str:
    """Return the system prompt markdown for a persona. Raises if missing.

    The servant-leader foundation (config/principles.py) is prepended so it
    lives in the model's reasoning context *before* the persona's role-specific
    instructions — the foundation runs first, not as a backstop.

    Marketing personas (cmo, marketing_internal, client_marketing_garage, iris)
    also get the Foundation Bible injected between the servant-leader header
    and the persona prompt — per CMO flag (cmo_state.md 2026-06-28T03:55:00Z).
    Non-marketing personas get an empty string back from foundation_bible_header
    so this is a no-op for them.
    """
    path = _PROMPTS_DIR / f"{persona.lower()}.md"
    if not path.exists():
        raise FileNotFoundError(f"No prompt for persona '{persona}' at {path}")
    bible = foundation_bible_header(persona)
    bible_block = f"\n---\n\n{bible}\n---\n" if bible else "\n"
    return f"{foundation_header()}{bible_block}{path.read_text(encoding='utf-8')}"


def load_persona_tools(persona: str) -> list[str]:
    """Return the tool allowlist for a persona."""
    import json
    path = _PROMPTS_DIR / f"{persona.lower()}_tools.json"
    if not path.exists():
        raise FileNotFoundError(f"No tool allowlist for persona '{persona}' at {path}")
    return json.loads(path.read_text(encoding="utf-8"))["allowed_tools"]
