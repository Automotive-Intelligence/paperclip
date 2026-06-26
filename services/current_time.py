"""Time-anchor injector for all AVO persona prompts.

Every LLM has a training-cutoff date months or years in the past. Without an
explicit time anchor, personas default to that cutoff date in their reasoning
("as of my last update", "I don't know today's date") OR confidently invent a
date. Both produce hallucinated context that downstream commits, copy, or
decisions reference.

Fix: prepend a deterministic Current-Time block to every persona prompt at
runtime, so the model knows the real wall-clock time before it reasons.

Used by:
  - services/flag_responder.py (autonomous draft producer)
  - services/persona_executor.py (APE) — by injection into user_message
  - avo-slack/app.py (live Slack chats with the 10 persona channels)

Format chosen for unambiguity AND human-readability in case it shows up in
logs / agent debug output:

    🕐 Current time: Friday, June 26, 2026 — 18:38 CDT (23:38:14 UTC)
       Date: 2026-06-26  |  Day of week: Friday  |  Week: 26

The date line is parseable; the prose line is what the LLM keys off.
"""

from __future__ import annotations

import datetime
from typing import Optional

try:
    import pytz
    _CDT = pytz.timezone("America/Chicago")
except Exception:  # pragma: no cover — pytz is in requirements but be defensive
    _CDT = None


def current_time_block(now: Optional[datetime.datetime] = None) -> str:
    """Return a multi-line Markdown block describing the current time.

    Pass `now` to override (useful for tests). Defaults to UTC now.

    Always safe to drop verbatim into a system prompt — no model-specific
    formatting. Five short lines, ~200 chars; not enough to displace real
    context.
    """
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    if _CDT is not None:
        now_cdt = now_utc.astimezone(_CDT)
        cdt_label = now_cdt.strftime("%H:%M %Z")
    else:
        # Fallback: hard-coded -5h offset; we'll be wrong by an hour during DST
        # gaps but it beats no local time at all.
        now_cdt = now_utc - datetime.timedelta(hours=5)
        cdt_label = now_cdt.strftime("%H:%M CDT")

    iso_date = now_utc.strftime("%Y-%m-%d")
    pretty = now_utc.strftime("%A, %B %-d, %Y")
    utc_label = now_utc.strftime("%H:%M:%S UTC")
    weekday = now_utc.strftime("%A")
    iso_week = now_utc.strftime("%V")

    return (
        f"🕐 Current time: {pretty} — {cdt_label} ({utc_label})\n"
        f"   Date: {iso_date}  |  Day of week: {weekday}  |  ISO week: {iso_week}"
    )


def current_time_one_liner(now: Optional[datetime.datetime] = None) -> str:
    """Single-line variant for tight token budgets (Slack snippets, logs)."""
    block = current_time_block(now)
    return block.splitlines()[0]
