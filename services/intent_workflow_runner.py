"""services/intent_workflow_runner.py -- Universal Intent-to-Revenue workflow runner.

Item 1 of the B&T flag posted 2026-07-03 in avo-telemetry/revenue_state.md.
Complete spec: avo-telemetry/marketing_deliverables/intent_workflow_spec_v1_2026-07-03.md.

Generalizes scripts/pp_build_icp_campaign.py (P&P-only) into a brand-parameterized
runner. Any brand's cold-email campaign is now:

    doppler run --project paperclip --config prd -- \\
        python -m services.intent_workflow_runner \\
        --brand panda build --dry-run

The runner reads config/brands/<brand>.yaml, validates against
config/brands/_schema.py::BrandConfig, then dispatches to the channel adapter
matching channel_roster[0]. Today the cold_email adapter (Instantly) is fully
wired; direct_mail / meta_lead_ad / sheet_drop are stubbed with clear error
messages pointing at item 5 of the flag.

Commands (mirror pp_build_icp_campaign.py so operators keep muscle memory):

    build [--dry-run]              build/update channel campaigns from brand.yaml
    load-leads --segment <key>     upload a CSV segment to the built campaign
    status                         list campaigns for this brand + status

Config-version stamping (spec section 3, non-negotiable): every scored record
the runner produces will carry the SHA-256 of the brand.yaml source bytes at
scoring time so the item 3 scoring service can attribute a decision to the
exact config. In this item-1 PR only build/load-leads run, and the hash is
computed + logged; the S3 stamp on CRM records lands in item 3.

Design principles preserved from pp_build_icp_campaign.py:
- Nothing is ever launched. Campaigns are created PAUSED. Sending is a manual
  step after warmup + copy approval.
- Idempotent: find-by-name + PATCH, never duplicate.
- Instantly's known-quirk HTML rendering is preserved verbatim (& handling,
  <div> line-wrapping, no tracking pixels).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

# Allow running as `python -m services.intent_workflow_runner` from repo root
# OR as `python services/intent_workflow_runner.py`. The `-m` path resolves via
# the package's __init__.py; the script path needs the sys.path prepend.
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.brands._schema import (  # noqa: E402
    BrandConfig,
    EmailStep,
    ICPContent,
    InstantlyConfig,
    SmartleadConfig,
)
from services import suppression  # noqa: E402  (pre-send DNC / opt-out gate)
from services import unsubscribe  # noqa: E402  (RFC 8058 one-click unsubscribe)

logger = logging.getLogger("intent_workflow_runner")

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"
SMARTLEAD_BASE = "https://server.smartlead.ai/api/v1"
_BRAND_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "brands"


def _cold_provider(brand: BrandConfig) -> Optional[str]:
    """Which cold_email provider block this brand carries: 'smartlead',
    'instantly', or None. Smartlead wins if both are present (a brand migrating
    off Instantly onto warmed Smartlead mailboxes)."""
    if getattr(brand, "smartlead", None) is not None:
        return "smartlead"
    if brand.instantly is not None:
        return "instantly"
    return None


# ---------------------------------------------------------------------------
# Config load + version stamping
# ---------------------------------------------------------------------------


def _load_brand_config(brand_key: str) -> Tuple[BrandConfig, str]:
    """Load config/brands/<brand>.yaml, validate, return (config, sha256 hash).

    The hash is computed on the RAW source bytes so the stamp is stable across
    Python versions / Pydantic normalizations. Cited in spec section 3 as
    non-negotiable for the S8 learning loop's attribution.
    """
    path = _BRAND_CONFIG_DIR / f"{brand_key}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in _BRAND_CONFIG_DIR.glob("*.yaml"))
        raise FileNotFoundError(
            f"brand config not found: {path}. Available: {available}"
        )
    raw = path.read_bytes()
    config_version = hashlib.sha256(raw).hexdigest()[:16]
    data = yaml.safe_load(raw.decode("utf-8"))
    try:
        config = BrandConfig(**data)
    except Exception as e:
        raise ValueError(f"{path} failed schema validation:\n{e}") from e
    if config.brand != brand_key:
        raise ValueError(
            f"{path} says brand='{config.brand}' but filename is '{brand_key}.yaml'"
        )
    return config, config_version


# ---------------------------------------------------------------------------
# Instantly channel adapter (cold_email)
#
# Adapted from scripts/pp_build_icp_campaign.py, parameterized by
# InstantlyConfig + BrandConfig.icp_content + BrandConfig.compliance_profile.
# Every P&P-specific literal moved into brand.yaml; the request shape,
# HTML-rendering quirks, and retry semantics are byte-preserved.
# ---------------------------------------------------------------------------


def _instantly_key(cfg: InstantlyConfig) -> str:
    key = (os.getenv(cfg.api_key_env) or "").strip()
    if not key:
        sys.exit(
            f"ERROR: {cfg.api_key_env} not in env. Run via: "
            f"doppler run --project paperclip --config prd -- ..."
        )
    return key


def _instantly_req(
    cfg: InstantlyConfig,
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """HTTP with the same retry semantics pp_build_icp_campaign.py had:
    6 attempts, exponential-ish backoff on network errors, 3+3*attempt on 429.
    Any non-2xx returns {'_error': True, 'status': ..., 'body': ...}."""
    headers = {"Authorization": f"Bearer {_instantly_key(cfg)}", "Content-Type": "application/json"}
    url = INSTANTLY_BASE + path
    for attempt in range(6):
        try:
            r = requests.request(method, url, headers=headers, json=body, params=params, timeout=30)
        except requests.exceptions.RequestException:
            time.sleep(min(30, 5 * (attempt + 1)))
            continue
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        if r.status_code not in (200, 201):
            return {"_error": True, "status": r.status_code, "body": r.text[:500]}
        return r.json() if r.text.strip() else {}
    return {"_error": True, "status": "network", "body": "exhausted retries"}


def _instantly_html(text: str) -> str:
    """Preserved verbatim from pp_build_icp_campaign.py::_html — this handles
    three verified Instantly quirks:
      (1) any '&' silently nukes the entire body (raw / &amp; / &#38; all).
          Swap '&' -> 'and'; brand copy reads naturally either way.
      (2) it strips loose text between top-level <br> tags.
      (3) '\\n' collapses to space on render, producing a wall of text.
    Fix: one <div> per line (blank -> spacer), which survives the sanitizer
    AND renders as real line breaks."""
    text = text.replace(" & ", " and ").replace("&", "and")
    out = []
    for ln in text.split("\n"):
        out.append("<div><br></div>" if ln.strip() == "" else f"<div>{ln}</div>")
    return "".join(out)


def _render_body(step: EmailStep, brand: BrandConfig) -> str:
    """Substitute {{cta}} + {{footer}} tokens; append footer if not already
    injected. {{firstName}} et al. pass through to Instantly's own mail-merge.

    COMPLIANCE: the footer is guaranteed to carry a REAL one-click unsubscribe
    link. We inject the literal `{{unsubscribe_url}}` merge tag (Instantly fills
    the per-recipient URL from the custom variable set at load-leads time). This
    upgrades the old "Reply STOP"-only footer to Google/Yahoo 2024 one-click.
    The merge tag contains no '&', so it survives _instantly_html's sanitizer;
    the real URL is a single path segment (no '&' either) minted per lead.
    """
    body = step.body
    cta = brand.cta_url or ""
    footer = brand.compliance_profile.unsubscribe_footer or ""
    body = body.replace("{{cta}}", cta)
    if "{{footer}}" in body:
        body = body.replace("{{footer}}", footer)
    elif footer:
        body = body.rstrip() + "\n\n" + footer
    # Ensure a working one-click unsubscribe link is present exactly once.
    if "{{unsubscribe_url}}" not in body:
        body = body.rstrip() + "\n\nUnsubscribe (one click): {{unsubscribe_url}}"
    return _instantly_html(body)


def _build_sequence(icp_key: str, content: ICPContent, brand: BrandConfig) -> List[Dict[str, Any]]:
    """Translate the per-ICP EmailStep list into Instantly's sequence shape.
    Byte-preserves pp_build_icp_campaign.py::_steps: one variant per step."""
    steps = []
    for step in content.steps:
        steps.append({
            "type": "email",
            "delay": step.delay_days,
            "variants": [{"subject": step.subject, "body": _render_body(step, brand)}],
        })
    return [{"steps": steps}]


def _campaign_schedule(cfg: InstantlyConfig) -> Dict[str, Any]:
    """Build Instantly's `campaign_schedule` block from the InstantlyConfig
    time-of-day + weekdays. Mirrors the P&P engine's SCHEDULE shape."""
    return {
        "schedules": [
            {
                "name": f"{cfg.schedule_timezone} business hours",
                "timing": {
                    "from": f"{cfg.schedule_start_hour:02d}:00",
                    "to": f"{cfg.schedule_end_hour:02d}:00",
                },
                "days": {str(d): True for d in cfg.schedule_days},
                "timezone": cfg.schedule_timezone,
            }
        ]
    }


def _find_campaign(cfg: InstantlyConfig, name: str) -> Optional[Dict[str, Any]]:
    """List campaigns, match by name (case-insensitive). Same as
    pp_build_icp_campaign.py::find_campaign."""
    res = _instantly_req(cfg, "GET", "/campaigns", params={"limit": 100})
    for c in (res.get("items") or []):
        if (c.get("name") or "").lower() == name.lower():
            return c
    return None


# ---------------------------------------------------------------------------
# Smartlead channel adapter (cold_email, warmed-secondary provider)
#
# Same interface as the Instantly adapter (build / load-leads / status) so the
# runner dispatches either provider transparently. WD's cold cohort runs here.
# The SAME compliance gate is wired into the Smartlead load path: placeholder-
# address guard, one-click-unsubscribe capability check, and the pre-send
# suppression filter — WD cannot send without them (fail-closed, exactly like
# Instantly).
#
# Smartlead differs from Instantly on the wire:
#   - auth is an `api_key` QUERY PARAM, not a Bearer header;
#   - campaign build is multi-step (create -> sequences -> schedule -> settings
#     -> attach mailboxes) instead of a single POST;
#   - sending accounts are numeric email_account_id, not addresses;
#   - merge tags are {{first_name}} / {{company_name}} / custom fields.
# ---------------------------------------------------------------------------


def _smartlead_ready(cfg: SmartleadConfig) -> bool:
    """True iff SMARTLEAD_WD_API_KEY (or whatever api_key_env the brand names)
    is present. The Smartlead path NO-OPS cleanly when absent (never sys.exit),
    so a WD run without the key is a benign skip, not a crash."""
    return bool((os.getenv(cfg.api_key_env) or "").strip())


def _smartlead_req(
    cfg: SmartleadConfig,
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """HTTP with the same retry semantics as the Instantly adapter: 6 attempts,
    backoff on network errors, 3+3*attempt on 429. Smartlead authenticates via
    an `api_key` query param (not a header). Any non-2xx returns
    {'_error': True, 'status': ..., 'body': ...}."""
    key = (os.getenv(cfg.api_key_env) or "").strip()
    q = dict(params or {})
    q["api_key"] = key
    url = SMARTLEAD_BASE + path
    for attempt in range(6):
        try:
            r = requests.request(
                method, url, json=body, params=q, timeout=30,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.RequestException:
            time.sleep(min(30, 5 * (attempt + 1)))
            continue
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        if r.status_code not in (200, 201):
            return {"_error": True, "status": r.status_code, "body": r.text[:500]}
        return r.json() if r.text.strip() else {}
    return {"_error": True, "status": "network", "body": "exhausted retries"}


def _smartlead_html(text: str) -> str:
    """Wrap plain-text body in HTML for Smartlead. Unlike Instantly, Smartlead
    does NOT nuke bodies containing '&', so no '& -> and' mangling is needed;
    we only turn newlines into real line breaks (one <div> per line, blank ->
    spacer) so the email doesn't render as a wall of text."""
    out = []
    for ln in text.split("\n"):
        out.append("<div><br></div>" if ln.strip() == "" else f"<div>{ln}</div>")
    return "".join(out)


def _smartlead_merge_tags(text: str) -> str:
    """Map the runner's generic merge tokens onto Smartlead's native syntax.
    {{firstName}} -> {{first_name}}, {{companyName}} -> {{company_name}}.
    {{unsubscribe_url}} passes through: it is delivered as a per-lead custom
    field at load time, and Smartlead resolves {{unsubscribe_url}} from it."""
    return (
        text.replace("{{firstName}}", "{{first_name}}")
        .replace("{{companyName}}", "{{company_name}}")
    )


def _render_body_smartlead(step: EmailStep, brand: BrandConfig) -> str:
    """Substitute {{cta}}/{{footer}}, guarantee a one-click unsubscribe link,
    map merge tags to Smartlead syntax, then wrap in Smartlead-safe HTML.
    Byte-parallel to the Instantly _render_body so the compliance footer +
    one-click unsubscribe guarantees are identical across providers."""
    body = step.body
    cta = brand.cta_url or ""
    footer = brand.compliance_profile.unsubscribe_footer or ""
    body = body.replace("{{cta}}", cta)
    if "{{footer}}" in body:
        body = body.replace("{{footer}}", footer)
    elif footer:
        body = body.rstrip() + "\n\n" + footer
    # Ensure a working one-click unsubscribe link is present exactly once.
    if "{{unsubscribe_url}}" not in body:
        body = body.rstrip() + "\n\nUnsubscribe (one click): {{unsubscribe_url}}"
    return _smartlead_html(_smartlead_merge_tags(body))


def _smartlead_sequences(content: ICPContent, brand: BrandConfig) -> List[Dict[str, Any]]:
    """Translate the per-ICP EmailStep list into Smartlead's sequence shape:
    a flat list of {seq_number, seq_delay_details.delay_in_days, subject,
    email_body}. delay_in_days preserves each step's delay_days verbatim."""
    seqs = []
    for i, step in enumerate(content.steps, 1):
        seqs.append({
            "seq_number": i,
            "seq_delay_details": {"delay_in_days": step.delay_days},
            "subject": step.subject,
            "email_body": _render_body_smartlead(step, brand),
        })
    return seqs


def _smartlead_schedule_body(cfg: SmartleadConfig) -> Dict[str, Any]:
    """Smartlead POST /campaigns/{id}/schedule body from the SmartleadConfig
    time-of-day + weekdays. days_of_the_week uses Smartlead's 0=Sun..6=Sat."""
    return {
        "timezone": cfg.schedule_timezone,
        "days_of_the_week": list(cfg.schedule_days),
        "start_hour": f"{cfg.schedule_start_hour:02d}:00",
        "end_hour": f"{cfg.schedule_end_hour:02d}:00",
        "min_time_btw_emails": cfg.min_time_btw_emails,
        "max_new_leads_per_day": cfg.daily_max_leads,
        "schedule_start_time": None,
    }


def _smartlead_settings_body(cfg: SmartleadConfig) -> Dict[str, Any]:
    """Smartlead POST /campaigns/{id}/settings body. Tracking is opt-IN via
    absence: we list the DONT_TRACK_* flags for whatever tracking is disabled,
    and stop-on-reply mirrors the Instantly stop_on_reply semantic."""
    track = []
    if not cfg.open_tracking:
        track.append("DONT_TRACK_EMAIL_OPEN")
    if not cfg.link_tracking:
        track.append("DONT_TRACK_LINK_CLICK")
    body: Dict[str, Any] = {
        "track_settings": track,
        "send_as_plain_text": cfg.text_only,
        "stop_lead_settings": "REPLY_TO_AN_EMAIL" if cfg.stop_on_reply else "NONE",
    }
    return body


def _smartlead_find_campaign(cfg: SmartleadConfig, name: str) -> Optional[Dict[str, Any]]:
    """List Smartlead campaigns, match by name (case-insensitive). GET
    /campaigns returns a bare list."""
    res = _smartlead_req(cfg, "GET", "/campaigns")
    items = res if isinstance(res, list) else (res.get("data") or res.get("items") or [])
    for c in items:
        if (c.get("name") or "").lower() == name.lower():
            return c
    return None


def _build_smartlead(brand: BrandConfig, config_version: str, dry_run: bool) -> int:
    """Idempotent Smartlead build: create (or reuse) each ICP campaign, then save
    sequences + schedule + settings + attach mailboxes. Never launched — Smartlead
    campaigns are created DRAFTED/PAUSED and we never POST status START."""
    cfg = brand.smartlead
    assert cfg is not None  # guarded by caller

    # COMPLIANCE GATE (fail-closed): placeholder-address guard, same as Instantly.
    try:
        suppression.assert_real_address(brand)
    except suppression.PlaceholderAddressError as e:
        logger.error("BLOCKED (placeholder address): %s", e)
        return 3

    if not dry_run and not _smartlead_ready(cfg):
        print(
            f"=== BUILD brand={brand.brand} provider=smartlead SKIPPED: "
            f"{cfg.api_key_env} not set (no-op). Set it in Doppler to build. ==="
        )
        return 0

    print(
        f"=== BUILD brand={brand.brand} provider=smartlead "
        f"config_version={config_version} dry_run={dry_run} ==="
    )
    if not cfg.sending_account_ids:
        logger.warning(
            "[%s] no smartlead.sending_account_ids configured — campaign will be "
            "built WITHOUT sending mailboxes attached (launch gate: provision + "
            "warm the %s mailboxes, then add their email_account_ids).",
            brand.brand, cfg.sending_domains,
        )

    for icp_key, content in brand.icp_content.items():
        seqs = _smartlead_sequences(content, brand)
        existing = _smartlead_find_campaign(cfg, content.campaign_name) if not dry_run else None
        if dry_run:
            print(f"\n[{icp_key}] would CREATE/UPDATE '{content.campaign_name}'")
            print(
                f"  mailboxes={len(cfg.sending_account_ids)} steps={len(content.steps)} "
                f"max_new_leads_per_day={cfg.daily_max_leads} text_only={cfg.text_only} "
                f"domains={cfg.sending_domains}"
            )
            print(f"  step1 subj: {content.steps[0].subject}")
            continue

        if existing:
            cid = existing.get("id")
            print(f"[{icp_key}] reuse campaign {cid} (PAUSED)")
        else:
            res = _smartlead_req(cfg, "POST", "/campaigns/create", body={"name": content.campaign_name})
            if res.get("_error") or not res.get("id"):
                print(f"[{icp_key}] CREATE FAILED: {res}")
                continue
            cid = res["id"]
            print(f"[{icp_key}] CREATED {cid} (DRAFTED/PAUSED, not launched)")

        # Sequences (POST replaces the campaign's sequence set — idempotent).
        r_seq = _smartlead_req(cfg, "POST", f"/campaigns/{cid}/sequences", body={"sequences": seqs})
        # Schedule + general settings.
        r_sch = _smartlead_req(cfg, "POST", f"/campaigns/{cid}/schedule", body=_smartlead_schedule_body(cfg))
        r_set = _smartlead_req(cfg, "POST", f"/campaigns/{cid}/settings", body=_smartlead_settings_body(cfg))
        # Attach warmed mailboxes if we have their ids (else a reported launch gate).
        r_acc = {"skipped": "no sending_account_ids"}
        if cfg.sending_account_ids:
            r_acc = _smartlead_req(
                cfg, "POST", f"/campaigns/{cid}/email-accounts",
                body={"email_account_ids": cfg.sending_account_ids},
            )
        errs = [
            name for name, r in (("seq", r_seq), ("schedule", r_sch), ("settings", r_set), ("accounts", r_acc))
            if isinstance(r, dict) and r.get("_error")
        ]
        print(f"[{icp_key}] configured {cid}: {'ERR ' + ','.join(errs) if errs else 'ok (PAUSED)'}")
    return 0


def _load_leads_smartlead(
    brand: BrandConfig,
    config_version: str,
    segment: str,
    allow_unreachable_suppression: bool = False,
) -> int:
    """Upload a CSV segment to the Smartlead campaign, AFTER the SAME fail-closed
    compliance preflight as the Instantly path: placeholder-address guard, one-
    click-unsubscribe capability, and the pre-send suppression filter. WD cannot
    enroll a single lead without all three passing."""
    cfg = brand.smartlead
    assert cfg is not None  # guarded by caller

    # --- Compliance preflight 1: placeholder-address guard (fail-closed) ----
    try:
        suppression.assert_real_address(brand)
    except suppression.PlaceholderAddressError as e:
        logger.error("BLOCKED (placeholder address): %s", e)
        return 3

    # --- Compliance preflight 2: one-click unsubscribe capability -----------
    if not unsubscribe.unsubscribe_ready():
        logger.error(
            "BLOCKED (no one-click unsubscribe): set UNSUBSCRIBE_SIGNING_SECRET "
            "and PUBLIC_UNSUB_BASE_URL before enrolling leads. Refusing to "
            "enroll without a working RFC 8058 unsubscribe link (fail-closed)."
        )
        return 3

    content = brand.icp_content.get(segment)
    if content is None:
        available = sorted(brand.icp_content.keys())
        logger.error("segment '%s' not in brand.icp_content. Available: %s", segment, available)
        return 2
    if not content.lead_file:
        logger.error("segment '%s' has no lead_file in brand.yaml; nothing to upload", segment)
        return 2

    # NO-OP guard: no API key -> clean skip (never crash), same posture as build.
    if not _smartlead_ready(cfg):
        print(
            f"[{segment}] provider=smartlead SKIPPED: {cfg.api_key_env} not set "
            f"(no-op). Set it in Doppler to enroll."
        )
        return 0

    leads_dir = brand.leads_dir or "~/avo-telemetry/marketing_deliverables"
    path = Path(os.path.expanduser(leads_dir)) / content.lead_file
    if not path.exists():
        logger.error("lead file not found: %s", path)
        return 2

    camp = _smartlead_find_campaign(cfg, content.campaign_name)
    if not camp:
        logger.error("campaign for segment '%s' not found in Smartlead; run `build` first.", segment)
        return 2
    cid = camp["id"]

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # --- Compliance preflight 3: suppression filter (fail-closed) -----------
    try:
        result = suppression.filter_suppressed(
            rows, brand, allow_unreachable=allow_unreachable_suppression
        )
    except suppression.SuppressionSourceUnreachable as e:
        logger.error(
            "BLOCKED (suppression source unreachable): %s. Re-run with "
            "--allow-unreachable-suppression ONLY if you accept the risk.", e,
        )
        return 3

    print(
        f"[{segment}] suppression gate: loaded={result.loaded} "
        f"suppressed={result.suppressed_count} enrolled={result.enrolled} "
        f"degraded={result.index.degraded} sources={result.index.sources}"
    )
    if result.index.degraded:
        logger.warning(
            "[%s] suppression index DEGRADED (override active) — a source failed; "
            "enrolling on partial suppression data.", segment,
        )
    rows = result.kept
    if not rows:
        print(f"[{segment}] nothing to enroll after suppression; done.")
        return 0

    # Smartlead accepts up to 100 leads per add-leads call.
    print(f"[{segment}] uploading {len(rows)} leads -> smartlead campaign {cid} (config_version={config_version})")
    added = failed = 0
    for start in range(0, len(rows), 100):
        chunk = rows[start:start + 100]
        lead_list = []
        for row in chunk:
            email = (row.get("email") or "").strip()
            lead: Dict[str, Any] = {
                "email": email,
                "first_name": (row.get("first_name") or "").strip() or None,
                "last_name": (row.get("last_name") or "").strip() or None,
                "company_name": (row.get("company") or row.get("company_name") or "").strip() or None,
                # Per-recipient one-click unsubscribe URL; the footer's
                # {{unsubscribe_url}} merge tag renders this at send time.
                "custom_fields": {"unsubscribe_url": unsubscribe.unsubscribe_url(email, brand.brand)},
            }
            lead_list.append({k: v for k, v in lead.items() if v is not None})
        body = {
            "lead_list": lead_list,
            "settings": {
                "ignore_global_block_list": False,
                "ignore_unsubscribe_list": False,
                "ignore_duplicate_leads_in_other_campaign": False,
            },
        }
        res = _smartlead_req(cfg, "POST", f"/campaigns/{cid}/leads", body=body)
        if res.get("_error"):
            failed += len(chunk)
            logger.error("[%s] chunk @%d failed: %s", segment, start, res)
        else:
            added += (res.get("upload_count") if isinstance(res.get("upload_count"), int) else len(chunk))
        time.sleep(0.3)
    print(
        f"[{segment}] DONE added={added} failed={failed} "
        f"(suppressed_before_enroll={result.suppressed_count})"
    )
    return 0


def _status_smartlead(brand: BrandConfig) -> int:
    """List Smartlead campaigns matching this brand's ICP campaign names."""
    cfg = brand.smartlead
    assert cfg is not None
    if not _smartlead_ready(cfg):
        print(f"(smartlead: {cfg.api_key_env} not set; no status to show.)")
        return 0
    names = {content.campaign_name.lower() for content in brand.icp_content.values()}
    res = _smartlead_req(cfg, "GET", "/campaigns")
    items = res if isinstance(res, list) else (res.get("data") or res.get("items") or [])
    matched = [c for c in items if (c.get("name") or "").lower() in names]
    if not matched:
        print(f"(no smartlead campaigns matched brand '{brand.brand}'.)")
        return 0
    for c in matched:
        print(f"  {c.get('id')}  status={c.get('status')}  {c.get('name')}")
    return 0


def _cmd_build(brand: BrandConfig, config_version: str, dry_run: bool) -> int:
    """Idempotent build: for each ICP content block, PATCH if a campaign of
    that name exists, otherwise CREATE. Nothing is ever launched here.

    Dispatches on the brand's cold_email provider (Instantly or Smartlead)."""
    provider = _cold_provider(brand)
    if "cold_email" not in brand.channel_roster or provider is None:
        primary = brand.channel_roster[0]
        logger.error(
            "brand '%s' has no wired cold_email provider (primary channel '%s'). "
            "cold_email needs an 'instantly' or 'smartlead' block; other channels "
            "(direct_mail / meta_lead_ad / sheet_drop) are item 5.",
            brand.brand, primary,
        )
        return 2

    if provider == "smartlead":
        return _build_smartlead(brand, config_version, dry_run)

    if brand.instantly is None:
        logger.error("brand '%s' has cold_email in roster but no 'instantly' block", brand.brand)
        return 2

    # COMPLIANCE GATE (fail-closed): refuse to build/enable a campaign for any
    # brand whose CAN-SPAM physical address is still a placeholder. A campaign
    # built with a fake address could be launched later; block it at the source.
    try:
        suppression.assert_real_address(brand)
    except suppression.PlaceholderAddressError as e:
        logger.error("BLOCKED (placeholder address): %s", e)
        return 3

    cfg = brand.instantly
    common = {
        "daily_limit": cfg.daily_limit,
        "daily_max_leads": cfg.daily_max_leads,
        "email_gap": cfg.email_gap_minutes,
        "random_wait_max": cfg.random_wait_max,
        "stop_on_reply": cfg.stop_on_reply,
        "stop_on_auto_reply": cfg.stop_on_auto_reply,
        "open_tracking": cfg.open_tracking,
        "link_tracking": cfg.link_tracking,
        "text_only": cfg.text_only,
    }
    schedule = _campaign_schedule(cfg)

    print(f"=== BUILD brand={brand.brand} config_version={config_version} dry_run={dry_run} ===")
    for icp_key, content in brand.icp_content.items():
        body = {
            "name": content.campaign_name,
            "campaign_schedule": schedule,
            "email_list": cfg.sending_accounts,
            "sequences": _build_sequence(icp_key, content, brand),
            **common,
        }
        existing = _find_campaign(cfg, content.campaign_name)
        if dry_run:
            print(
                f"\n[{icp_key}] would {'UPDATE' if existing else 'CREATE'} '{content.campaign_name}'"
            )
            print(
                f"  accounts={len(cfg.sending_accounts)} steps={len(content.steps)} "
                f"daily_limit={cfg.daily_limit} text_only={cfg.text_only}"
            )
            print(f"  step1 subj: {content.steps[0].subject}")
            continue
        if existing:
            cid = existing["id"]
            res = _instantly_req(cfg, "PATCH", f"/campaigns/{cid}", body=body)
            print(f"[{icp_key}] UPDATED {cid}: {'ERR ' + str(res) if res.get('_error') else 'ok (PAUSED)'}")
        else:
            res = _instantly_req(cfg, "POST", "/campaigns", body=body)
            if res.get("_error"):
                print(f"[{icp_key}] CREATE FAILED: {res}")
            else:
                print(f"[{icp_key}] CREATED {res.get('id')} (PAUSED, not launched)")
    return 0


def _cmd_load_leads(
    brand: BrandConfig,
    config_version: str,
    segment: str,
    allow_unreachable_suppression: bool = False,
) -> int:
    """Upload a CSV of leads to the segment's built campaign, AFTER passing
    every lead through the pre-send suppression / opt-out gate.

    Compliance preflight (all fail-closed) runs before a single lead uploads:
      1. Placeholder-address guard  -> block if the CAN-SPAM address is fake.
      2. One-click unsubscribe capability -> block if we cannot mint a
         verifiable RFC 8058 unsubscribe link (no working opt-out == no send).
      3. Suppression filter -> drop unsubscribed / bounced / do-not-contact /
         existing-customer addresses BEFORE enrollment. If a configured
         suppression source is unreachable, block everyone unless the operator
         passed --allow-unreachable-suppression.

    Dispatches on the brand's cold_email provider (Instantly or Smartlead); the
    identical fail-closed compliance preflight runs on both paths.
    """
    provider = _cold_provider(brand)
    if provider is None:
        logger.error("brand '%s' has no cold_email provider block; load-leads not applicable", brand.brand)
        return 2
    if provider == "smartlead":
        return _load_leads_smartlead(
            brand, config_version, segment,
            allow_unreachable_suppression=allow_unreachable_suppression,
        )
    if brand.instantly is None:
        logger.error("brand '%s' has no 'instantly' block; load-leads not applicable", brand.brand)
        return 2

    # --- Compliance preflight 1: placeholder-address guard (fail-closed) ----
    try:
        suppression.assert_real_address(brand)
    except suppression.PlaceholderAddressError as e:
        logger.error("BLOCKED (placeholder address): %s", e)
        return 3

    # --- Compliance preflight 2: one-click unsubscribe capability -----------
    # We attach a per-lead unsubscribe URL below; if we cannot mint a verifiable
    # link (no signing secret / no public base URL) we must NOT enroll, because
    # a send whose unsubscribe link does not work is itself non-compliant.
    if not unsubscribe.unsubscribe_ready():
        logger.error(
            "BLOCKED (no one-click unsubscribe): set UNSUBSCRIBE_SIGNING_SECRET "
            "and PUBLIC_UNSUB_BASE_URL before enrolling leads. Refusing to "
            "enroll without a working RFC 8058 unsubscribe link (fail-closed)."
        )
        return 3

    content = brand.icp_content.get(segment)
    if content is None:
        available = sorted(brand.icp_content.keys())
        logger.error("segment '%s' not in brand.icp_content. Available: %s", segment, available)
        return 2
    if not content.lead_file:
        logger.error(
            "segment '%s' has no lead_file in brand.yaml; nothing to upload", segment,
        )
        return 2

    leads_dir = brand.leads_dir or "~/avo-telemetry/marketing_deliverables"
    path = Path(os.path.expanduser(leads_dir)) / content.lead_file
    if not path.exists():
        logger.error("lead file not found: %s", path)
        return 2

    cfg = brand.instantly
    camp = _find_campaign(cfg, content.campaign_name)
    if not camp:
        logger.error(
            "campaign for segment '%s' not found in Instantly; run `build` first.", segment,
        )
        return 2
    cid = camp["id"]

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # --- Compliance preflight 3: suppression filter (fail-closed) -----------
    # Drop anyone unsubscribed / bounced / do-not-contact / existing-customer
    # BEFORE enrollment. build fails closed on an unreachable configured source
    # unless the operator explicitly overrode it.
    try:
        result = suppression.filter_suppressed(
            rows, brand, allow_unreachable=allow_unreachable_suppression
        )
    except suppression.SuppressionSourceUnreachable as e:
        logger.error(
            "BLOCKED (suppression source unreachable): %s. Re-run with "
            "--allow-unreachable-suppression ONLY if you accept the risk.", e,
        )
        return 3

    print(
        f"[{segment}] suppression gate: loaded={result.loaded} "
        f"suppressed={result.suppressed_count} enrolled={result.enrolled} "
        f"degraded={result.index.degraded} sources={result.index.sources}"
    )
    if result.index.degraded:
        logger.warning(
            "[%s] suppression index DEGRADED (override active) — a source failed; "
            "enrolling on partial suppression data.", segment,
        )
    rows = result.kept
    if not rows:
        print(f"[{segment}] nothing to enroll after suppression; done.")
        return 0

    print(f"[{segment}] uploading {len(rows)} leads -> campaign {cid} (config_version={config_version})")
    added = failed = 0
    for i, row in enumerate(rows, 1):
        email = (row["email"] or "").strip()
        b = {
            "email": email,
            "campaign": cid,
            "first_name": (row.get("first_name") or "").strip() or None,
            "last_name": (row.get("last_name") or "").strip() or None,
            # Per-recipient one-click unsubscribe URL; the footer's
            # {{unsubscribe_url}} merge tag renders this at send time.
            "custom_variables": {
                "unsubscribe_url": unsubscribe.unsubscribe_url(email, brand.brand),
            },
            "skip_if_in_campaign": True,
        }
        b = {k: v for k, v in b.items() if v is not None}
        res = _instantly_req(cfg, "POST", "/leads", body=b)
        if res.get("_error"):
            failed += 1
        else:
            added += 1
        if i % 250 == 0:
            print(f"  {i}/{len(rows)}  added={added} failed={failed}")
        time.sleep(0.15)
    print(
        f"[{segment}] DONE added={added} failed={failed} "
        f"(suppressed_before_enroll={result.suppressed_count})"
    )
    return 0


def _cmd_status(brand: BrandConfig) -> int:
    """List cold_email campaigns for this brand (Instantly or Smartlead).
    Filters to campaign names that match the brand so mixed workspaces stay
    readable."""
    provider = _cold_provider(brand)
    if provider == "smartlead":
        return _status_smartlead(brand)
    if brand.instantly is None:
        logger.error("brand '%s' has no cold_email provider block; no status to show", brand.brand)
        return 2
    cfg = brand.instantly
    res = _instantly_req(cfg, "GET", "/campaigns", params={"limit": 100})
    prefix = brand.display_name.lower()
    matched = [
        c for c in (res.get("items") or [])
        if (c.get("name") or "").lower().startswith(prefix)
        or any(
            (c.get("name") or "").lower() == content.campaign_name.lower()
            for content in brand.icp_content.values()
        )
    ]
    if not matched:
        print(f"(no campaigns matched brand '{brand.brand}'.)")
        return 0
    for c in matched:
        print(f"  {c.get('id')}  status={c.get('status')}  {c.get('name')}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Universal Intent-to-Revenue workflow runner (item 1).",
    )
    ap.add_argument(
        "--brand",
        required=True,
        help="Brand key. Matches config/brands/<brand>.yaml.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build/update channel campaigns.")
    b.add_argument("--dry-run", action="store_true")

    ll = sub.add_parser("load-leads", help="Upload a CSV of leads for a segment.")
    ll.add_argument("--segment", required=True, help="ICP segment key (matches brand.icp_content).")
    ll.add_argument(
        "--allow-unreachable-suppression",
        action="store_true",
        help=(
            "DANGER: enroll even if a configured suppression source is "
            "unreachable. Default is fail-closed (block all enrollment)."
        ),
    )

    sub.add_parser("status", help="List campaigns for this brand.")

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    brand, config_version = _load_brand_config(args.brand)
    logger.info(
        "brand=%s config_version=%s channel_roster=%s success_metric=%s",
        brand.brand, config_version, brand.channel_roster, brand.success_metric,
    )

    if args.cmd == "build":
        return _cmd_build(brand, config_version, dry_run=args.dry_run)
    if args.cmd == "load-leads":
        return _cmd_load_leads(
            brand,
            config_version,
            segment=args.segment,
            allow_unreachable_suppression=args.allow_unreachable_suppression,
        )
    if args.cmd == "status":
        return _cmd_status(brand)
    return 1


if __name__ == "__main__":
    sys.exit(main())
