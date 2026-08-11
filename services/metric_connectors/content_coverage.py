"""content_coverage connector — Publishing Coverage % (CMO north-star KPI).

Coverage % = distinct posting-days SHIPPED / INTENDED, per brand x channel,
over a rolling 7-day window. The mandate (config/studio_social_brands.yaml) is
DAILY coverage — one post per brand per platform per day — so intended = 7
posting-days per week per connected channel. Shipped = distinct local days with
at least one non-backfill scheduled post in the durable social_registry.

Denominator = CONFIGURED platforms (studio_social_brands.yaml `platforms`)
INTERSECT CONNECTED platforms (slipstream_brands.yaml `zernio_accounts`):
  * A deliberately-off channel (Book'd social, not enabled) never counts.
  * A CONFIGURED-but-not-CONNECTED channel (e.g. WD IG needsReconnect) is a
    CONNECTION GAP — surfaced in raw_payload and excluded from the denominator,
    so a dead account doesn't silently tank the % while hiding the real cause.

Numerator source: the COMMITTED social_registry.jsonl, now durable via the
SOCIAL_REGISTRY_COMMIT=1 commit-back (paperclip #256) — the same file the CMO
brief reads. Read via services.cmo_shipped._load_registry_text (flag_router).

Signal discipline (mirrors cmo_shipped.signal_ok): an EMPTY registry read is a
SIGNAL FAILURE -> status='no_data', never a false 0%. A registry that loads but
has no rows for a brand is a REAL 0% (status='ok').

Three brand-naming systems are joined here (verified against the live registry
2026-08-07): the registry uses short slugs (avi/aipg/wd/agent_empire, plus
`wd_legacy_cd` for WD's Calling Digital profile and `founder` for the personal
channel); studio/slipstream configs use long keys; the scorecard uses short
slugs. Platform strings also differ: the registry writes `twitter`, the config
writes `x` — canonicalized to `x` on both sides.

v1 scope: SOCIAL daily-coverage (the actionable headline number, fully sourced
from the durable registry). Blog freshness (<96h SLA) is a documented
fast-follow: reuse services.cmo_shipped.list_blog_commits + config/watchdog.yaml
blog_max_age_hours and add it as an additional per-brand channel.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import yaml

from services.metric_connectors.types import KPIReading, RunContext

logger = logging.getLogger(__name__)

_UTC = timezone.utc
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STUDIO_CFG = os.path.join(_REPO_ROOT, "config", "studio_social_brands.yaml")
_SLIPSTREAM_CFG = os.path.join(_REPO_ROOT, "config", "slipstream_brands.yaml")

# scorecard_slug, [registry brand slugs to fold], studio_social key, zernio_accounts key
BRANDS = [
    ("avi",          ["avi"],                 "automotive_intelligence", "autointelligence"),
    ("aipg",         ["aipg"],                "ai_phone_guy",            "aiphoneguy"),
    ("wd",           ["wd", "wd_legacy_cd"],  "worship_digital",         "worshipdigital"),
    ("agent_empire", ["agent_empire"],        "agent_empire",            "agentempire"),
]
_REG_BRAND_TO_SLUG = {rb: slug for slug, regs, _s, _z in BRANDS for rb in regs}


def _canon_platform(p: Optional[str]) -> str:
    """Registry writes 'twitter'; config writes 'x'. Canonicalize both to 'x'.
    Everything else lowercased as-is (facebook/instagram/linkedin/tiktok/youtube)."""
    p = (p or "").strip().lower()
    return "x" if p in ("x", "twitter") else p


def _is_backfill(row: dict) -> bool:
    ep = (row.get("entry_point") or "")
    return ep.startswith("backfill") or "backfill" in (row.get("content_id") or "")


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)
    except (ValueError, TypeError):
        return None


def compute_coverage(registry_text: str, studio_cfg: dict, slipstream_cfg: dict,
                     now: datetime, kpi_name: str = "publishing_coverage") -> List[KPIReading]:
    """Pure: given the registry text + both configs + `now`, return per-brand
    coverage readings plus one org-level (brand=None) portfolio reading.

    An empty/whitespace registry_text => signal unavailable => every reading is
    status='no_data' (NOT a false 0%)."""
    signal_ok = bool((registry_text or "").strip())
    zernio_accounts = (slipstream_cfg or {}).get("zernio_accounts") or {}
    studio_brands = (studio_cfg or {}).get("brands") or {}
    win_start = now - timedelta(days=7)

    # Numerator: distinct (slug, platform) -> set of local posting-days in window.
    shipped_days: Dict[tuple, set] = defaultdict(set)
    if signal_ok:
        for line in registry_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if _is_backfill(row):
                continue
            slug = _REG_BRAND_TO_SLUG.get(row.get("brand"))
            if slug is None:  # founder / bookd (not enabled) / unknown -> not brand coverage
                continue
            when = _parse_iso(row.get("scheduled_for") or row.get("ts"))
            if when is None or when < win_start or when > now:
                continue
            shipped_days[(slug, _canon_platform(row.get("platform")))].add(when.date())

    readings: List[KPIReading] = []
    tot_ship = tot_int = 0
    for slug, _regs, studio_key, zernio_key in BRANDS:
        studio_b = studio_brands.get(studio_key) or {}
        if not studio_b.get("enabled", False):
            continue  # brand held out of the engine entirely -> not in the denominator
        intended_days = min(int(studio_b.get("posts_per_run") or 7), 7)
        configured = [_canon_platform(p) for p in (studio_b.get("platforms") or [])]
        connected = {_canon_platform(p) for p in (zernio_accounts.get(zernio_key) or {}).keys()}
        denom_plats = [p for p in configured if p in connected]
        gap_plats = [p for p in configured if p not in connected]  # connection gaps

        b_ship = b_int = 0
        plat_break: Dict[str, dict] = {}
        for p in denom_plats:
            s = min(len(shipped_days.get((slug, p), ())), intended_days)
            plat_break[p] = {"shipped_days": s, "intended_days": intended_days,
                             "pct": round(100.0 * s / intended_days, 1) if intended_days else None}
            b_ship += s
            b_int += intended_days
        cov = (100.0 * b_ship / b_int) if b_int else None
        tot_ship += b_ship
        tot_int += b_int
        readings.append(KPIReading(
            persona="cmo", kpi_name=kpi_name, brand=slug,
            value_numeric=round(cov, 1) if (signal_ok and cov is not None) else None,
            unit="%",
            status="ok" if (signal_ok and b_int) else "no_data",
            error_detail=None if signal_ok else "social_registry read empty (signal unavailable)",
            raw_payload={"platforms": plat_break, "connection_gaps": gap_plats,
                         "shipped_days": b_ship, "intended_days": b_int,
                         "window_start": win_start.isoformat(), "window_end": now.isoformat()}))

    port = (100.0 * tot_ship / tot_int) if tot_int else None
    readings.append(KPIReading(
        persona="cmo", kpi_name=kpi_name, brand=None,
        value_numeric=round(port, 1) if (signal_ok and port is not None) else None,
        unit="%",
        status="ok" if (signal_ok and tot_int) else "no_data",
        error_detail=None if signal_ok else "social_registry read empty (signal unavailable)",
        raw_payload={"shipped_days": tot_ship, "intended_days": tot_int, "scope": "portfolio"}))
    return readings


def ryg(value: Optional[float], kpi_spec: dict) -> str:
    """Red/yellow/green for a higher-is-better % against the cmo.yaml thresholds.
    The current scorecard_aggregator colours by STATUS only (value-vs-threshold is
    its deferred 'Phase C'); the weekly-brief render uses THIS so a 33% coverage
    reads red, not green-because-it-has-data. None/no signal -> 'no_data'."""
    if value is None:
        return "no_data"
    target = kpi_spec.get("target")
    yellow = kpi_spec.get("threshold_yellow")
    if target is not None and value >= target:
        return "green"
    if yellow is not None and value >= yellow:
        return "yellow"
    return "red"


def _load_yaml(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("[content_coverage] could not load %s: %s", path, e)
        return {}


def _default_registry_text() -> str:
    """The committed social_registry.jsonl, via the same authenticated path the
    CMO brief uses. Returns '' on any failure -> compute_coverage renders no_data."""
    try:
        from services.cmo_shipped import _load_registry_text
        return _load_registry_text() or ""
    except Exception as e:  # noqa: BLE001 - best-effort; empty text -> no_data, never a false 0%
        logger.warning("[content_coverage] registry read failed: %s", e)
        return ""


def fetch(kpi_spec: dict, run_ctx: RunContext) -> List[KPIReading]:
    name = kpi_spec.get("name") or "publishing_coverage"
    now = getattr(run_ctx, "started_at", None) or datetime.now(_UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_UTC)
    return compute_coverage(_default_registry_text(), _load_yaml(_STUDIO_CFG),
                            _load_yaml(_SLIPSTREAM_CFG), now, kpi_name=name)
