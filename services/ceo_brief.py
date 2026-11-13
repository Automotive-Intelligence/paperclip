"""services/ceo_brief.py -- the one daily email, plus the page it links to.

Replaces the Morning Briefing, the AI-spend email, the APE digest, the CMO Daily,
and every per-event "needs you" stream with ONE send a day to Michael. Spec approved
2026-11-06 (docs/superpowers/plans/2026-11-06-ceo-brief.md).

Order is exception-first, and every empty section collapses to one line:

  1. Needs you today   brief_items (Sonar, Postal, failures) + Client Portal feed
  2. Money             Twenty pipeline, Stripe MRR (live key only), AI spend
  3. Scoreboard        3-5 lines per business; minors only when something moved
  4. Broken signals    one line per dead feed, repeated every day until fixed
  5. Shipped yesterday blog + social per brand (cmo_shipped), APE routine ships,
                       portal jobs that went live

Fail closed, out loud: a feed that cannot be read is a Broken signal line, never a
zero that looks like a quiet day. The Brief sends on a zero day too, so silence
always means a dead sender, never a quiet company.

The dashboard page is the same data rendered in full at GET /brief?k=<token>. The
token is derived from ADMIN_KEY (no new secret), so the link in the email just works.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import html as _html
import json
import logging
import os
import zoneinfo
from typing import Any, Callable, Dict, List, Optional

import requests

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_RESEND = "https://api.resend.com/emails"
_CT = zoneinfo.ZoneInfo("America/Chicago")

DEFAULT_TO = "michael@automotiveintelligence.io"
DEFAULT_FROM = "AVO CEO Brief <briefing@mail.automotiveintelligence.io>"
DEFAULT_BASE_URL = "https://paperclip-production-ba14.up.railway.app"

# The five businesses that always get a scoreboard block, in order.
BUSINESSES = (
    ("ep", "Equity Pulse"),
    ("aipg", "The AI Phone Guy"),
    ("wd", "Worship Digital"),
    ("sx", "Service Experience / Lou Fusz"),
    ("avi", "Automotive Intelligence"),
)
# One-line entries, shown only when a number moved.
MINORS = (
    ("cd", "Calling Digital"),
    ("twinning", "TWinning / ChevyPlug"),
    ("wend", "WEND"),
    ("bookd", "Book'd"),
)

# cmo_shipped brand keys -> business keys used here.
_SHIPPED_TO_BIZ = {
    "worshipdigital": "wd", "autointelligence": "avi", "aiphoneguy": "aipg",
    "agentempire": "avi", "bookd": "bookd",
}
# Twenty pipeline brand labels (rivers/shared/morning_briefing) -> business keys.
_PIPELINE_TO_BIZ = {"WD": "wd", "AvI": "avi", "AIPG": "aipg", "Book'd": "bookd"}
# kpi_snapshots brand values -> business keys.
_KPI_BRAND_TO_BIZ = {
    "wd": "wd", "avi": "avi", "aipg": "aipg", "bookd": "bookd", "pp": None,
    "worshipdigital.co": "wd", "automotiveintelligence.io": "avi",
    "theaiphoneguy.com": "aipg", "bookd.cx": "bookd", "buildagentempire.com": "avi",
}

_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS ceo_brief_runs (
    id           SERIAL PRIMARY KEY,
    brief_date   DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload      JSONB NOT NULL,
    sent         BOOLEAN NOT NULL DEFAULT FALSE,
    resend_id    TEXT
);
"""


# ── config ────────────────────────────────────────────────────────────────────

def recipient() -> str:
    return (os.getenv("CEO_BRIEF_TO") or DEFAULT_TO).strip()


def sender() -> str:
    return (os.getenv("CEO_BRIEF_FROM") or DEFAULT_FROM).strip()


def base_url() -> str:
    explicit = (os.getenv("CEO_BRIEF_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    dom = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    return f"https://{dom}" if dom else DEFAULT_BASE_URL


def view_token() -> str:
    """Unguessable page token derived from the admin key; no new secret to store."""
    seed = (os.getenv("ADMIN_KEY") or "").strip()
    if not seed:
        keys = [k.strip() for k in (os.getenv("API_KEYS") or "").split(",") if k.strip()]
        seed = sorted(keys)[0] if keys else ""
    if not seed:
        return ""
    return hashlib.sha256(f"ceo-brief:{seed}".encode()).hexdigest()[:32]


def token_ok(candidate: str) -> bool:
    tok = view_token()
    return bool(tok) and bool(candidate) and hmac.compare_digest(candidate, tok)


def page_url() -> str:
    tok = view_token()
    return f"{base_url()}/brief?k={tok}" if tok else f"{base_url()}/brief"


def needs_you_days() -> int:
    try:
        return max(1, int(os.getenv("BRIEF_NEEDS_YOU_DAYS", "3")))
    except ValueError:
        return 3


# ── collection ────────────────────────────────────────────────────────────────

class _Ctx:
    """Accumulates broken signals while the collectors run."""

    def __init__(self) -> None:
        self.broken: List[Dict[str, str]] = []
        self._seen: set = set()

    def signal(self, key: str, text: str, fix: str = "") -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self.broken.append({"key": key, "text": text, "fix": fix})


def _safe(ctx: _Ctx, key: str, fn: Callable[[], Any], default: Any, label: str) -> Any:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a dead feed is a line, not a crash
        logger.warning("[ceo-brief] %s failed: %s", key, e)
        ctx.signal(key, f"{label}: unreadable ({type(e).__name__}: {str(e)[:120]})")
        return default


def _age_hours(ts: Any, now: _dt.datetime) -> Optional[float]:
    if not isinstance(ts, _dt.datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return round((now - ts).total_seconds() / 3600, 1)


def _needs_you(ctx: _Ctx, now: _dt.datetime) -> List[Dict[str, Any]]:
    from services import brief_items
    rows = _safe(ctx, "brief_items", lambda: brief_items.open_items(needs_you_days()), [],
                 "Needs-you ledger (brief_items)")
    out = []
    for r in rows:
        out.append({"kind": r["kind"], "brand": r.get("brand"), "title": r["title"],
                    "detail": r.get("detail") or "", "url": r.get("url") or "",
                    "age_h": _age_hours(r.get("created_at"), now)})
    return out


def _portal(ctx: _Ctx) -> Dict[str, Any]:
    """Client Portal feed: open jobs that need Michael + jobs that went live."""
    url = (os.getenv("PORTAL_BRIEF_URL") or "").strip().rstrip("/")
    key = (os.getenv("PORTAL_BRIEF_KEY") or "").strip()
    empty = {"wired": False, "open": [], "live_24h": [], "counts": {}}
    if not url or not key:
        ctx.signal("portal-feed",
                   "Client Portal feed not wired: approvals and stalls are not in this Brief",
                   "Set PORTAL_BRIEF_KEY on Vercel (client-portal) and PORTAL_BRIEF_URL + "
                   "PORTAL_BRIEF_KEY on Railway (paperclip).")
        return empty

    def _get():
        r = requests.get(f"{url}/api/brief", headers={"Authorization": f"Bearer {key}"},
                         timeout=20)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json() or {}
        return {"wired": True, "open": list(data.get("open") or []),
                "live_24h": list(data.get("live_24h") or []),
                "counts": dict(data.get("counts") or {})}

    return _safe(ctx, "portal-feed", _get, empty, "Client Portal feed")


def _shipped(ctx: _Ctx) -> Dict[str, Any]:
    """Blog + social per brand (24h), APE routine ships, and whether the engine
    signal could be read at all."""
    from services import cmo_shipped
    rows = _safe(ctx, "cmo-shipped",
                 lambda: cmo_shipped.collect(lookback_hours=24), [], "Blog engine signal")
    brands = []
    dead = [r["name"] for r in rows if not r.get("signal_ok", True)]
    for r in rows:
        lines = cmo_shipped.shipped_lines(r) if r.get("signal_ok", True) else []
        held = cmo_shipped.held_lines(r) if r.get("signal_ok", True) else []
        brands.append({"key": r.get("key"), "name": r.get("name"), "lines": lines,
                       "held": held, "signal_ok": r.get("signal_ok", True),
                       "post_count": r.get("post_count", 0), "social": r.get("social", 0)})
    if dead:
        errors = {r["name"]: (r.get("signal_error") or "unknown error")
                  for r in rows if not r.get("signal_ok", True)}
        detail = "; ".join(f"{n}: {e[:120]}" for n, e in errors.items())
        if any("401" in e for e in errors.values()):
            ctx.signal("slipstream-gh-token",
                       "Blog engine signal unavailable for " + ", ".join(dead)
                       + ": SLIPSTREAM_GH_TOKEN on Railway returns 401. Blog, social, "
                       "script engine and the APE handoff poller are all dark on it.",
                       "Mint a fine-grained GitHub token (salesdroid) with Contents + Pull "
                       "requests on the brand repos and avo-telemetry; set it as "
                       "SLIPSTREAM_GH_TOKEN on the paperclip Railway service.")
        else:
            ctx.signal("blog-signal",
                       "Blog engine signal unavailable for " + ", ".join(dead) + " (" + detail + ")",
                       "Check the brand repo name and the token's repo access in "
                       "services/cmo_shipped.BRANDS.")

    def _ape():
        # The digest queue table is created by the APE writer on first use; on an
        # install where no routine ship was ever queued it does not exist, and that
        # is "no ships", not a broken feed.
        exists = fetch_all("SELECT to_regclass('ape_routine_digest_queue')")
        if not exists or exists[0][0] is None:
            return []
        rows_ = fetch_all(
            "SELECT id, persona, envelope FROM ape_routine_digest_queue "
            "WHERE sent_at IS NULL ORDER BY queued_at ASC LIMIT 50")
        out = []
        for _id, persona, env in rows_:
            env = json.loads(env) if isinstance(env, str) else (env or {})
            out.append({"id": _id, "persona": persona,
                        "summary": env.get("action_summary", "?")})
        return out

    ape = _safe(ctx, "ape-digest", _ape, [], "APE routine ships")
    return {"brands": brands, "ape": ape, "posts": sum(b["post_count"] for b in brands),
            "social": sum(b["social"] for b in brands)}


def mark_ape_delivered(ids: List[Any]) -> None:
    """Called ONLY after Resend accepted the Brief: a skipped or failed send must
    leave the routine ships queued for the next Brief (fail closed)."""
    if not ids:
        return
    try:
        execute_query("UPDATE ape_routine_digest_queue SET sent_at = NOW() "
                      "WHERE id = ANY(%s)", (list(ids),))
    except Exception as e:  # noqa: BLE001
        logger.warning("[ceo-brief] mark_ape_delivered failed: %s", e)


def _agent_runs_24h() -> int:
    rows = fetch_all("SELECT COUNT(*) FROM agent_logs WHERE created_at >= NOW() - INTERVAL '24 hours'")
    return int(rows[0][0]) if rows else 0


def _ai_spend(ctx: _Ctx, now_ct: _dt.datetime, engine_activity: int) -> Dict[str, Any]:
    """Ledger truth with an honest classification of a zero day:
       metered  rows exist
       idle     no rows AND no engine ran (nothing to meter)
       gap      no rows BUT engines ran -> a metering gap, listed as a broken signal"""
    from services.llm_ledger import month_to_date_total, window_totals
    # Trailing 24h on BOTH sides (ledger and agent_logs), so an evening run cannot
    # land in "today" for one and "yesterday" for the other and fake a gap.
    totals = _safe(ctx, "llm-ledger", lambda: window_totals(24),
                   {"day": "last 24h", "total_usd": 0.0, "calls": 0, "by_persona": []},
                   "AI spend ledger")
    mtd = _safe(ctx, "llm-ledger-mtd", lambda: month_to_date_total(now_ct.date()), None,
                "AI spend month to date")
    calls = int(totals.get("calls") or 0)
    if calls > 0:
        state = "metered"
    elif engine_activity > 0:
        state = "gap"
        ctx.signal("ledger-gap",
                   f"AI spend ledger recorded 0 calls in the last 24h while {engine_activity} "
                   "agent run(s) logged: a metering gap, not a quiet day.",
                   "Only instrumented call sites write llm_spend_ledger rows (slipstream, "
                   "studio-social, persona executor, litellm hook). Find the engine that "
                   "ran without metering.")
    else:
        state = "idle"
    return {"day": totals.get("day"), "yesterday_usd": float(totals.get("total_usd") or 0.0),
            "calls": calls, "mtd_usd": mtd, "state": state,
            "by_persona": totals.get("by_persona") or [],
            "note": "Ledger covers paperclip only. Claude usage outside paperclip "
                    "(cloud routines, Claude Code) is billed on the Anthropic console."}


def _mrr(ctx: _Ctx) -> Dict[str, Any]:
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        ctx.signal("mrr-feed", "Revenue feed not wired: no MRR or cash-collected number in this Brief",
                   "Stripe is not configured on paperclip; AIPG billing lives in GHL.")
        return {"wired": False, "value": None, "note": "no Stripe key"}
    if not key.startswith("sk_live_"):
        ctx.signal("mrr-feed", "Revenue feed not wired: Stripe key on paperclip is TEST mode, "
                   "so MRR reads $0 regardless of real billing",
                   "Swap STRIPE_SECRET_KEY for the live restricted key, or wire GHL payments.")
        return {"wired": False, "value": None, "note": "Stripe key is test mode"}

    def _get():
        from tools.stripe import active_subscription_mrr, stripe_ready
        if not stripe_ready():
            raise RuntimeError("stripe SDK not installed")
        return float(active_subscription_mrr())

    val = _safe(ctx, "stripe-mrr", _get, None, "Stripe MRR")
    return {"wired": val is not None, "value": val, "note": ""}


def _pipeline(ctx: _Ctx) -> Dict[str, Any]:
    from rivers.shared.morning_briefing import pull_revenue_pipeline_summary
    p = _safe(ctx, "twenty-pipeline", pull_revenue_pipeline_summary,
              {"wired": False, "by_brand": {}}, "Twenty pipeline")
    if not p.get("wired"):
        ctx.signal("twenty-pipeline", "Twenty pipeline unreadable: no brand workspace answered, "
                   "so the Money section has no pipeline number")
        return p
    unwired = [b for b, v in (p.get("by_brand") or {}).items() if not v.get("wired")]
    if unwired:
        ctx.signal("twenty-unwired",
                   "Twenty pipeline unwired for " + ", ".join(unwired)
                   + ": their deals are not in the Money section")
    return p


def _money(ctx: _Ctx, now_ct: _dt.datetime, engine_activity: int) -> Dict[str, Any]:
    return {"pipeline": _pipeline(ctx), "mrr": _mrr(ctx),
            "ai_spend": _ai_spend(ctx, now_ct, engine_activity)}


def _leads(ctx: _Ctx) -> Dict[str, Dict[str, int]]:
    from services.lead_store import recent_count

    def _get():
        out = {}
        for b in ("aipg", "avi", "wd", "ep"):
            out[b] = {"d1": int(recent_count(b, 24)), "d7": int(recent_count(b, 24 * 7))}
        return out

    return _safe(ctx, "lead-store", _get, {}, "Lead store")


def _kpis(ctx: _Ctx) -> List[Dict[str, Any]]:
    """Latest OK reading per (persona, kpi, brand) from the collector, last 7 days."""
    def _get():
        rows = fetch_all(
            "SELECT DISTINCT ON (persona, kpi_name, brand) persona, kpi_name, brand, "
            "value_numeric, unit, status, ts_collected FROM kpi_snapshots "
            "WHERE ts_collected > NOW() - INTERVAL '7 days' "
            "ORDER BY persona, kpi_name, brand, ts_collected DESC")
        return [{"persona": r[0], "kpi": r[1], "brand": r[2],
                 "value": float(r[3]) if r[3] is not None else None, "unit": r[4],
                 "status": r[5]} for r in rows if r[5] == "ok"]

    return _safe(ctx, "kpi-snapshots", _get, [], "KPI snapshots")


def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _scoreboard(ctx: _Ctx, shipped: Dict[str, Any], money: Dict[str, Any],
                portal: Dict[str, Any], leads: Dict[str, Dict[str, int]],
                kpis: List[Dict[str, Any]]) -> Dict[str, Any]:
    lines: Dict[str, List[str]] = {k: [] for k, _ in BUSINESSES + MINORS}
    moved: set = set()

    by_brand = (money.get("pipeline") or {}).get("by_brand") or {}
    for label, biz in _PIPELINE_TO_BIZ.items():
        v = by_brand.get(label) or {}
        if not v.get("wired"):
            continue
        lines[biz].append(
            f"Pipeline {_fmt_money(v.get('pipeline_value'))} open across "
            f"{int(v.get('deals_active') or 0)} deal(s); {int(v.get('bookings_24h') or 0)} "
            f"booked, {int(v.get('closed_24h') or 0)} closed yesterday")
        if any(int(v.get(k) or 0) for k in ("bookings_24h", "closed_24h")):
            moved.add(biz)

    for b, n in leads.items():
        biz = {"aipg": "aipg", "avi": "avi", "wd": "wd", "ep": "ep"}.get(b)
        if biz:
            lines[biz].append(f"Leads: {n['d1']} yesterday, {n['d7']} last 7 days")
            if n["d1"]:
                moved.add(biz)

    for b in shipped.get("brands") or []:
        biz = _SHIPPED_TO_BIZ.get(b.get("key"))
        if not biz:
            continue
        if not b.get("signal_ok", True):
            lines[biz].append("Content: engine signal unavailable (see Broken signals)")
        else:
            lines[biz].append(f"Content: {b.get('post_count', 0)} blog post(s), "
                              f"{b.get('social', 0)} social post(s) yesterday")
            if b.get("post_count") or b.get("social"):
                moved.add(biz)

    for k in kpis:
        biz = _KPI_BRAND_TO_BIZ.get((k.get("brand") or "").lower())
        if biz and k.get("value") is not None and len(lines[biz]) < 5:
            unit = k.get("unit") or ""
            lines[biz].append(f"{k['kpi'].replace('_', ' ')}: {k['value']:g}{(' ' + unit) if unit else ''}")

    if portal.get("wired"):
        c = portal.get("counts") or {}
        lines["sx"].append(
            f"Portal: {int(c.get('open') or 0)} job(s) open, {int(c.get('needs_michael') or 0)} "
            f"need you, {len(portal.get('live_24h') or [])} went live yesterday")
        if portal.get("live_24h") or c.get("needs_michael"):
            moved.add("sx")
    else:
        lines["sx"].append("Portal feed not wired (see Broken signals)")

    if not lines["ep"]:
        lines["ep"].append("No live feed in paperclip yet (GA4 and Klaviyo are read elsewhere)")
        ctx.signal("ep-feed", "Equity Pulse has no feed in this Brief",
                   "Wire the GA4 read token and Klaviyo list counts into paperclip.")

    majors = [{"key": k, "name": n, "lines": lines[k][:5]} for k, n in BUSINESSES]
    minors = [{"key": k, "name": n, "lines": lines[k][:2]}
              for k, n in MINORS if k in moved and lines[k]]
    return {"majors": majors, "minors": minors}


def collect(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Build the whole Brief. Read-only: one-shot queues (APE ships) are marked
    delivered by run_daily only after Resend accepted the email."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    now_ct = now.astimezone(_CT)
    ctx = _Ctx()

    needs = _needs_you(ctx, now)
    portal = _portal(ctx)
    for j in portal.get("open") or []:
        needs.append({"kind": "portal", "brand": j.get("client"),
                      "title": f"{j.get('label') or j.get('status')}: {j.get('client')}",
                      "detail": (j.get("request") or "")[:240], "url": j.get("url") or "",
                      "age_h": j.get("age_hours")})
    engine_activity = _safe(ctx, "agent-logs", _agent_runs_24h, 0, "Agent run log")
    shipped = _shipped(ctx)
    money = _money(ctx, now_ct, engine_activity)
    leads = _leads(ctx)
    kpis = _kpis(ctx)
    scoreboard = _scoreboard(ctx, shipped, money, portal, leads, kpis)
    for j in portal.get("live_24h") or []:
        shipped.setdefault("portal_live", []).append(j)

    return {
        "date": now_ct.date().isoformat(),
        "generated_at": now.isoformat(),
        "needs_you": needs,
        "money": money,
        "scoreboard": scoreboard,
        "broken_signals": ctx.broken,
        "shipped": shipped,
        "page_url": page_url(),
    }


# ── rendering ─────────────────────────────────────────────────────────────────

def _e(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""))


def _nice_date(iso: str) -> str:
    try:
        d = _dt.date.fromisoformat(iso)
        return d.strftime("%a %b %-d")
    except (TypeError, ValueError):
        return iso or ""


def subject(data: Dict[str, Any]) -> str:
    n = len(data.get("needs_you") or [])
    b = len(data.get("broken_signals") or [])
    head = f"{n} need{'s' if n == 1 else ''} you" if n else "Nothing needs you"
    tail = f" · {b} broken signal{'s' if b != 1 else ''}" if b else ""
    return f"CEO Brief {_nice_date(data.get('date', ''))}: {head}{tail}"


def _needs_html(items: List[Dict[str, Any]], full: bool) -> str:
    if not items:
        return "<p class='one'>Nothing needs you today.</p>"
    rows = []
    for i in items[: (200 if full else 12)]:
        link = f" <a href='{_e(i['url'])}'>open</a>" if i.get("url") else ""
        age = f" <span class='dim'>{i['age_h']:.0f}h ago</span>" if i.get("age_h") is not None else ""
        det = f"<div class='dim'>{_e(i['detail'][: (600 if full else 160)])}</div>" if i.get("detail") else ""
        rows.append(f"<li><b>{_e(i['kind'])}</b> {_e(i['title'])}{link}{age}{det}</li>")
    more = len(items) - 12
    tail = f"<li class='dim'>+{more} more on the page</li>" if (not full and more > 0) else ""
    return f"<ul>{''.join(rows)}{tail}</ul>"


def _money_html(m: Dict[str, Any]) -> str:
    p = m.get("pipeline") or {}
    mrr = m.get("mrr") or {}
    ai = m.get("ai_spend") or {}
    out = []
    if p.get("wired"):
        out.append(f"<li>Pipeline open: <b>{_fmt_money(p.get('pipeline_value'))}</b> across "
                   f"{int(p.get('deals_active') or 0)} deal(s); yesterday {int(p.get('bookings_24h') or 0)} "
                   f"booked, {int(p.get('closed_24h') or 0)} closed; {int(p.get('deals_stalled_7d') or 0)} stalled over 7d</li>")
    else:
        out.append("<li>Pipeline: Twenty unreadable (see Broken signals)</li>")
    if mrr.get("wired"):
        out.append(f"<li>MRR (Stripe, live): <b>{_fmt_money(mrr.get('value'))}</b></li>")
    else:
        out.append("<li>Revenue / MRR: not wired (see Broken signals)</li>")
    mtd = ai.get("mtd_usd")
    mtd_txt = f"${float(mtd):,.2f}" if mtd is not None else "n/a"
    state = ai.get("state")
    if state == "metered":
        out.append(f"<li>AI spend: <b>${ai.get('yesterday_usd', 0):,.2f}</b> yesterday "
                   f"({ai.get('calls', 0)} calls), {mtd_txt} month to date</li>")
    elif state == "idle":
        out.append(f"<li>AI spend: $0.00 yesterday, no engine ran; {mtd_txt} month to date</li>")
    else:
        out.append(f"<li>AI spend: ledger empty while engines ran (metering gap, see Broken signals); "
                   f"{mtd_txt} month to date</li>")
    return f"<ul>{''.join(out)}</ul>"


def _scoreboard_html(s: Dict[str, Any]) -> str:
    out = []
    for b in s.get("majors") or []:
        lines = b.get("lines") or ["no data"]
        out.append(f"<li><b>{_e(b['name'])}</b><ul>" + "".join(f"<li>{_e(x)}</li>" for x in lines) + "</ul></li>")
    for b in s.get("minors") or []:
        out.append(f"<li><b>{_e(b['name'])}</b>: {_e('; '.join(b.get('lines') or []))}</li>")
    return f"<ul>{''.join(out)}</ul>"


def _broken_html(items: List[Dict[str, str]], full: bool) -> str:
    if not items:
        return "<p class='one'>All feeds reporting.</p>"
    rows = []
    for b in items:
        fix = f"<div class='dim'>Fix: {_e(b['fix'])}</div>" if (full and b.get("fix")) else ""
        rows.append(f"<li>{_e(b['text'])}{fix}</li>")
    return f"<ul>{''.join(rows)}</ul>"


def _shipped_html(s: Dict[str, Any]) -> str:
    out = []
    for b in s.get("brands") or []:
        if not b.get("signal_ok", True):
            continue
        parts = list(b.get("lines") or [])
        if b.get("held"):
            parts += [f"held: {h}" for h in b["held"]]
        if parts:
            out.append(f"<li><b>{_e(b['name'])}</b>: {_e('; '.join(parts))}</li>")
    for a in s.get("ape") or []:
        out.append(f"<li><b>{_e(a.get('persona'))}</b> autopilot: {_e(a.get('summary'))}</li>")
    for j in s.get("portal_live") or []:
        out.append(f"<li><b>{_e(j.get('client'))}</b> portal: {_e((j.get('request') or '')[:120])} is live</li>")
    if not out:
        return "<p class='one'>Nothing shipped yesterday.</p>"
    return f"<ul>{''.join(out)}</ul>"


_CSS = ("body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111;"
        "max-width:680px;margin:0 auto;padding:20px;line-height:1.45;font-size:15px}"
        "h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;text-transform:uppercase;"
        "letter-spacing:.06em;color:#555;margin:22px 0 6px;border-top:1px solid #e5e5e5;padding-top:12px}"
        "ul{padding-left:20px;margin:4px 0}li{margin:3px 0}.dim{color:#777;font-size:13px}"
        ".one{margin:4px 0;color:#333}a{color:#0b57d0}.top{font-size:13px;color:#555}")


def render_html(data: Dict[str, Any], url: str, *, full: bool = False) -> str:
    """Email (compact) or page (full) body. Same sections, same order."""
    n = len(data.get("needs_you") or [])
    title = f"CEO Brief · {_nice_date(data.get('date', ''))}"
    top = (f"<p class='top'>Full detail: <a href='{_e(url)}'>{_e(url)}</a></p>" if not full
           else f"<p class='top'>Generated {_e(data.get('generated_at', ''))} UTC · "
                f"<a href='{_e(url)}&fresh=1'>refresh</a> · <a href='{_e(url.replace('/brief?', '/brief.json?'))}'>json</a></p>")
    body = (
        f"<h1>{_e(title)}</h1>{top}"
        f"<h2>1. Needs you today{f' ({n})' if n else ''}</h2>{_needs_html(data.get('needs_you') or [], full)}"
        f"<h2>2. Money</h2>{_money_html(data.get('money') or {})}"
        f"<h2>3. Scoreboard</h2>{_scoreboard_html(data.get('scoreboard') or {})}"
        f"<h2>4. Broken signals</h2>{_broken_html(data.get('broken_signals') or [], full)}"
        f"<h2>5. Shipped yesterday</h2>{_shipped_html(data.get('shipped') or {})}"
        "<p class='dim' style='margin-top:24px'>One email a day, sent even on zero days. "
        "Real leads and a shoot-day failure still arrive immediately. Everything else is here.</p>"
    )
    if full:
        return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
                f"content='width=device-width,initial-scale=1'><title>{_e(title)}</title>"
                f"<style>{_CSS}</style></head><body>{body}</body></html>")
    return f"<!doctype html><html><body><style>{_CSS}</style>{body}</body></html>"


def render_text(data: Dict[str, Any], url: str) -> str:
    L = [f"CEO BRIEF {_nice_date(data.get('date', ''))}", f"Full detail: {url}", ""]
    needs = data.get("needs_you") or []
    L.append("1. NEEDS YOU TODAY")
    if not needs:
        L.append("   Nothing needs you today.")
    for i in needs[:12]:
        L.append(f"   - [{i['kind']}] {i['title']}" + (f" {i['url']}" if i.get("url") else ""))
    m = data.get("money") or {}
    ai = m.get("ai_spend") or {}
    p = m.get("pipeline") or {}
    L += ["", "2. MONEY",
          f"   Pipeline open: {_fmt_money(p.get('pipeline_value')) if p.get('wired') else 'Twenty unreadable'}",
          f"   AI spend yesterday: ${ai.get('yesterday_usd', 0):,.2f} ({ai.get('state')})", "", "3. SCOREBOARD"]
    for b in (data.get("scoreboard") or {}).get("majors") or []:
        L.append(f"   {b['name']}")
        for x in b.get("lines") or []:
            L.append(f"     - {x}")
    for b in (data.get("scoreboard") or {}).get("minors") or []:
        L.append(f"   {b['name']}: {'; '.join(b.get('lines') or [])}")
    L += ["", "4. BROKEN SIGNALS"]
    broken = data.get("broken_signals") or []
    if not broken:
        L.append("   All feeds reporting.")
    for b in broken:
        L.append(f"   - {b['text']}")
    L += ["", "5. SHIPPED YESTERDAY"]
    s = data.get("shipped") or {}
    any_ship = False
    for b in s.get("brands") or []:
        if b.get("signal_ok", True) and b.get("lines"):
            any_ship = True
            L.append(f"   {b['name']}: {'; '.join(b['lines'])}")
    for a in s.get("ape") or []:
        any_ship = True
        L.append(f"   {a.get('persona')} autopilot: {a.get('summary')}")
    if not any_ship:
        L.append("   Nothing shipped yesterday.")
    return "\n".join(L)


# ── persistence + send ────────────────────────────────────────────────────────

def _json_default(o: Any) -> Any:
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    return str(o)


def store_run(data: Dict[str, Any], *, sent: bool, resend_id: Optional[str]) -> None:
    try:
        execute_query(_RUNS_DDL)
        execute_query(
            "INSERT INTO ceo_brief_runs (brief_date, payload, sent, resend_id) VALUES (%s, %s::jsonb, %s, %s)",
            (data.get("date"), json.dumps(data, default=_json_default), bool(sent), resend_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("[ceo-brief] store_run failed: %s", e)


def latest_run() -> Optional[Dict[str, Any]]:
    try:
        execute_query(_RUNS_DDL)
        rows = fetch_all("SELECT payload FROM ceo_brief_runs ORDER BY generated_at DESC LIMIT 1")
        if not rows:
            return None
        payload = rows[0][0]
        return json.loads(payload) if isinstance(payload, str) else payload
    except Exception as e:  # noqa: BLE001
        logger.warning("[ceo-brief] latest_run failed: %s", e)
        return None


def send(data: Dict[str, Any]) -> Dict[str, Any]:
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key:
        return {"status": "skip", "reason": "RESEND_API_KEY not set"}
    url = data.get("page_url") or page_url()
    payload = {"from": sender(), "to": [recipient()], "subject": subject(data),
               "html": render_html(data, url), "text": render_text(data, url)}
    try:
        r = requests.post(_RESEND, timeout=30, json=payload,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json",
                                   "User-Agent": "avo-ceo-brief/1.0"})
        if r.ok:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {"status": "sent", "to": recipient(), "subject": payload["subject"],
                    "id": body.get("id")}
        logger.error("[ceo-brief] Resend http=%s body=%s", r.status_code, r.text[:300])
        return {"status": "failed", "http": r.status_code, "body": r.text[:300]}
    except requests.RequestException as e:
        logger.exception("[ceo-brief] send raised")
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def run_daily() -> Dict[str, Any]:
    """APScheduler entry point: collect, send, persist; drain one-shot queues only
    once the send is confirmed."""
    try:
        data = collect()
        result = send(data)
        sent_ok = result.get("status") == "sent"
        store_run(data, sent=sent_ok, resend_id=result.get("id"))
        if sent_ok:
            mark_ape_delivered([a.get("id") for a in (data.get("shipped") or {}).get("ape") or []
                                if a.get("id") is not None])
        try:
            from services.watchdog import record_heartbeat
            record_heartbeat("ceo_brief")
        except Exception:  # noqa: BLE001
            logger.warning("[ceo-brief] heartbeat write failed (send itself done)")
        receipt = {**result, "date": data.get("date"), "needs_you": len(data.get("needs_you") or []),
                   "broken_signals": len(data.get("broken_signals") or [])}
        logger.info("[ceo-brief] %s", receipt)
        return receipt
    except Exception as e:  # noqa: BLE001
        logger.exception("[ceo-brief] run_daily crashed")
        return {"status": "crashed", "error": f"{type(e).__name__}: {e}"}
