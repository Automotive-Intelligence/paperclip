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

from services.llm_ledger import daily_totals, openrouter_usage, provider_delta, snapshot_provider, tavily_usage, zernio_spend

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


def _gap_banner(totals: dict) -> str:
    """FAIL CLOSED: zero ledger rows is a metering gap until proven otherwise.
    For weeks the meter said "$0.00" while engines burned real money because no
    live call site wrote rows; a silent zero must never read as a quiet day."""
    if int(totals.get("calls") or 0) > 0:
        return ""
    return (
        "<div style='margin:10px 0;padding:10px 12px;border-left:4px solid #c00;"
        "background:#fff4f4;font-size:13px;'>"
        "<b>Ledger recorded 0 calls.</b> Treat this as a <b>metering gap</b>, not a quiet day, "
        "unless no engine ran: only instrumented call sites write rows. Compare against the "
        "OpenRouter provider line below, which is true regardless of code path.</div>"
    )


def _provider_section(orx: dict | None) -> str:
    """OpenRouter ground truth: what the provider says we spent since the last
    daily snapshot, plus remaining balance. Independent of the ledger."""
    if not orx:
        return ("<div style='color:#999;font-size:12px;margin:8px 0;'>OpenRouter provider "
                "counter unavailable this run.</div>")
    delta = orx.get("delta")
    if delta is None:
        spent = "baseline captured today; daily delta starts tomorrow"
    else:
        spent = f"${delta['spent_usd']:.2f} since {delta['since'].strftime('%Y-%m-%d %H:%M')} UTC"
    return (
        "<h3 style='margin:22px 0 6px;'>OpenRouter (provider truth)</h3>"
        f"<div style='font-size:13px;'>Spent: <b>{spent}</b> &middot; Balance: "
        f"<b>${orx['balance']:.2f}</b> &middot; Lifetime usage: ${orx['total_usage']:.2f}</div>"
    )


def _tavily_section(tv: dict | None) -> str:
    """Tavily search credits: plan burn + paygo exposure. Provider truth for the
    prospect-enrichment pipeline's search spend."""
    if not tv:
        return ""
    cap = tv.get("paygo_limit")
    cap_txt = (f"capped at {cap}" if cap is not None else
               "<b style='color:#c00;'>UNCAPPED</b> -- set a paygo limit in the Tavily dashboard")
    pct = (100.0 * tv["plan_usage"] / tv["plan_limit"]) if tv.get("plan_limit") else 0
    day_line = ""
    if tv.get("delta_usd") is not None:
        day_line = (f"Searches since {tv['since'].strftime('%m-%d %H:%M')} UTC: "
                    f"<b>{tv['delta_credits']}</b> (&asymp; ${tv['delta_usd']:.2f}) &middot; ")
    return (
        "<h3 style='margin:22px 0 6px;'>Tavily search credits (provider truth)</h3>"
        f"<div style='font-size:13px;'>{day_line}Plan: <b>{tv['plan_usage']}/{tv['plan_limit']}</b> "
        f"({pct:.0f}%) &middot; Paygo used this period: <b>{tv['paygo_usage']}</b> &middot; "
        f"Paygo {cap_txt}</div>"
    )


# Tavily unit economics: Project plan $30/4,000 credits = $7.50/1k; paygo bills
# $8/1k. Blended-at-plan-rate is close enough for a daily dollar figure.
_TAVILY_USD_PER_CREDIT = 0.0075
_ZERNIO_USD_PER_ACCT_MONTH = 7.44  # ~$0.01/account-hour, decoded from the Aug receipt


def _zernio_section(z: dict | None) -> str:
    """Zernio: billed per connected account per HOUR since the 07-29 migration.
    The lever is DISCONNECTING idle accounts, so the run-rate math is the point."""
    if not z:
        return ""
    n = z.get("connected_accounts")
    rate = (f" &middot; ~{n} connected accounts &asymp; "
            f"<b>${n * _ZERNIO_USD_PER_ACCT_MONTH:.0f}/mo run-rate</b> "
            f"(each disconnect saves ~${_ZERNIO_USD_PER_ACCT_MONTH:.2f}/mo)") if n else ""
    return (
        "<h3 style='margin:22px 0 6px;'>Zernio (provider truth)</h3>"
        f"<div style='font-size:13px;'>This period: <b>${z['period_usd']:.2f}</b> "
        f"(plan: {z.get('plan')}; X API ${z['x_usd']:.2f}){rate}. August closed at $92.20 "
        f"-- billing is per connected account-hour; idle connections cost the same as active ones.</div>"
    )


def _tavily_truth() -> dict | None:
    """Tavily usage + a snapshot-based daily credit delta, dollarized."""
    try:
        tv = tavily_usage()
        if not tv:
            return None
        total = tv["plan_usage"] + tv["paygo_usage"]
        delta = provider_delta("tavily-credits", float(total))
        snapshot_provider("tavily-credits", float(total), None)
        if delta is not None:
            tv["delta_credits"] = int(delta["spent_usd"])  # field reuse: raw counter delta
            tv["delta_usd"] = delta["spent_usd"] * _TAVILY_USD_PER_CREDIT
            tv["since"] = delta["since"]
        return tv
    except Exception as e:
        logger.warning("[spend_email] tavily truth failed: %s", e)
        return None


def _openrouter_truth() -> dict | None:
    """Snapshot the provider counter and compute yesterday's delta. Never raises."""
    try:
        u = openrouter_usage()
        if not u:
            return None
        delta = provider_delta("openrouter", u["total_usage"])
        snapshot_provider("openrouter", u["total_usage"], u.get("balance"))
        return {**u, "delta": delta}
    except Exception as e:
        logger.warning("[spend_email] openrouter truth failed: %s", e)
        return None


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
<div style="color:#666;font-size:13px;">{totals['calls']} ledger-recorded API call(s)</div>
{_gap_banner(totals)}
{_provider_section(totals.get("openrouter"))}
{_tavily_section(totals.get("tavily"))}
{_zernio_section(totals.get("zernio"))}
{section("By persona", totals["by_persona"], "persona")}
{section("By model", totals["by_model"], "model")}
{client_section}
<hr style="margin-top:28px;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:11px;">
Sources: <code>llm_spend_ledger</code> (per-call rows from instrumented call sites:
slipstream, studio-social, persona/litellm) + OpenRouter's own cumulative usage counter
(provider truth for every OpenRouter call, any code path). NOT metered: fal image/video
spend and Anthropic calls made outside paperclip (cloud routines, the GitHub triage agent).
Cross-check Anthropic Console → Usage for those.
</div>
</body></html>
"""


def _subject(totals: dict) -> str:
    """Headline the provider delta when the ledger is empty, so the subject
    line can never read "$0.00" over real spend."""
    orx = totals.get("openrouter") or {}
    delta = orx.get("delta")
    orx_part = f" · OpenRouter ${delta['spent_usd']:.2f}" if delta else ""
    if int(totals.get("calls") or 0) == 0:
        return f"💸 AI spend {totals['day']}: ledger EMPTY (metering gap){orx_part}"
    return f"💸 AI spend {totals['day']}: ${totals['total_usd']:.2f} ({totals['calls']} calls){orx_part}"


def send_daily_spend_email(day=None) -> bool:
    """Send yesterday's (or `day`'s) spend rollup. Returns True on send."""
    if day is None:
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    totals = daily_totals(day)
    totals["openrouter"] = _openrouter_truth()
    totals["tavily"] = _tavily_truth()
    try:
        totals["zernio"] = zernio_spend()
    except Exception:
        totals["zernio"] = None

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("[spend_email] RESEND_API_KEY not set — skipping (totals: $%.2f)",
                       totals["total_usd"])
        return False

    subject = _subject(totals)
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
