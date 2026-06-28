# services/spend_email.py
"""Daily AI-spend email — the first real spend-visibility deliverable.

Reads yesterday's rows from llm_spend_ledger and sends Michael a one-screen
rollup: total, by persona, by model, by client. Reuses the same Resend wiring
as ape_audit_email.

Scheduled once daily (see app.py). Sends even on a $0 day so the meter is
visibly alive — the email is the proof the ledger is recording.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from services.llm_ledger import daily_totals

logger = logging.getLogger(__name__)

RECIPIENT = os.getenv("PERSONA_EXECUTOR_RECIPIENT") or os.getenv(
    "BRIEFING_RECIPIENT", "michael@automotiveintelligence.io"
)
SENDER = os.getenv("PERSONA_EXECUTOR_FROM", "AVO APE <ape@mail.automotiveintelligence.io>")
RESEND_URL = "https://api.resend.com/emails"


def _rows_html(rows, label_key: str) -> str:
    if not rows:
        return "<tr><td style='padding:6px 10px;color:#999;' colspan='3'>none</td></tr>"
    return "".join(
        f"<tr>"
        f"<td style='padding:6px 10px;'>{r.get(label_key)}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>${r['cost_usd']:.4f}</td>"
        f"<td style='padding:6px 10px;text-align:right;color:#666;'>{r['calls']}</td>"
        f"</tr>"
        for r in rows
    )


def _build_html(totals: dict) -> str:
    def section(title, rows, key):
        return (
            f"<h3 style='margin:22px 0 6px;'>{title}</h3>"
            f"<table style='width:100%;border-collapse:collapse;font-size:13px;'>"
            f"<tr style='color:#888;font-size:11px;text-transform:uppercase;'>"
            f"<td style='padding:4px 10px;'>{key}</td>"
            f"<td style='padding:4px 10px;text-align:right;'>cost</td>"
            f"<td style='padding:4px 10px;text-align:right;'>calls</td></tr>"
            f"{_rows_html(rows, key)}</table>"
        )

    client_section = (
        section("By client", totals["by_client"], "client")
        if totals.get("by_client")
        else ""
    )
    return f"""
<!DOCTYPE html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:680px;margin:0 auto;padding:20px;">
<h2 style="margin:0 0 4px;">💸 AI spend — {totals['day']}</h2>
<div style="font-size:28px;font-weight:700;margin:6px 0 2px;">${totals['total_usd']:.2f}</div>
<div style="color:#666;font-size:13px;">{totals['calls']} API call(s) across all agents + personas</div>
{section("By persona", totals["by_persona"], "persona")}
{section("By model", totals["by_model"], "model")}
{client_section}
<hr style="margin-top:28px;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:11px;">
Source: <code>llm_spend_ledger</code> (per-call ledger). Cross-check totals against
Anthropic Console → Usage &amp; Cost. Per-client rows populate as client-facing
agents are instrumented or run under per-client API keys.
</div>
</body></html>
"""


def send_daily_spend_email(day=None) -> bool:
    """Send yesterday's (or `day`'s) spend rollup. Returns True on send."""
    if day is None:
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    totals = daily_totals(day)

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("[spend_email] RESEND_API_KEY not set — skipping (totals: $%.2f)",
                       totals["total_usd"])
        return False

    subject = f"💸 AI spend {totals['day']}: ${totals['total_usd']:.2f} ({totals['calls']} calls)"
    try:
        r = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": SENDER,
                "to": [RECIPIENT],
                "subject": subject,
                "html": _build_html(totals),
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            logger.info("[spend_email] sent daily spend email for %s ($%.2f)",
                        totals["day"], totals["total_usd"])
            return True
        logger.error("[spend_email] Resend error %s: %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.error("[spend_email] send errored: %s", e)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(send_daily_spend_email())
