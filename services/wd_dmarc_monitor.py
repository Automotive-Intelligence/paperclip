"""
services/wd_dmarc_monitor.py — DMARC compliance monitor for WD outbound stack.

Tracks the DMARC records on three WD-related sending domains:

  worshipdigital.co        — PRIMARY brand domain + Loops transactional mail
                             MUST stay at p=reject or p=quarantine

  bestworshipdigital.com   — intent-data cold outbound (Smartlead arm)
  allworshipdigital.com    — intent-data cold outbound (Instantly arm)
                             Both intentionally relaxed to p=none during
                             14d warmup (~through 2026-07-06). MUST be
                             re-tightened to enforcement after warmup.

Wired up:
  scheduler  : weekly Sunday 8:00 CDT (cro_audit territory; catches drift)
  admin route: POST /admin/wd-dmarc-audit (manual trigger)
  dispatch   : on finding, posts to #build-tech via /pit-wall/dispatch

Findings classification:
  CRITICAL   : primary domain has dropped from enforcement (p=none/missing)
  WARN       : warmup domain still at p=none past the WARMUP_END date
  OK         : primary at enforcement + warmup domains in expected state

Per IM-WD flag #9 (2026-06-26).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────────

PRIMARY_DOMAIN = "worshipdigital.co"
WARMUP_DOMAINS = ["bestworshipdigital.com", "allworshipdigital.com"]
# Smartlead/Instantly 14d warmup ends ~2026-07-06; give 7d grace.
WARMUP_GRACE_END = _dt.date(2026, 7, 13)

# Acceptable DMARC policies for the primary domain.
ENFORCEMENT_POLICIES = {"reject", "quarantine"}

_DISPATCH_URL_DEFAULT = (
    "https://avo-production-e7f2.up.railway.app/pit-wall/dispatch"
)


# ── DMARC parser ──────────────────────────────────────────────────────────


@dataclass
class DmarcRecord:
    domain: str
    raw: str
    p: Optional[str] = None      # primary policy: none / quarantine / reject
    sp: Optional[str] = None     # subdomain policy
    fo: Optional[str] = None     # failure-reporting options
    rua: Optional[str] = None    # aggregate-report URI
    ruf: Optional[str] = None    # forensic-report URI
    found: bool = False          # True when a v=DMARC1 record was returned
    error: Optional[str] = None  # set on lookup failure

    def is_enforcing(self) -> bool:
        return (self.p or "").lower() in ENFORCEMENT_POLICIES


def _parse_dmarc_txt(raw: str) -> Dict[str, str]:
    """Parse `v=DMARC1; p=none; rua=...` style TXT into a dict."""
    out: Dict[str, str] = {}
    for token in raw.split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        k, _, v = token.partition("=")
        out[k.strip().lower()] = v.strip()
    return out


def lookup_dmarc(domain: str) -> DmarcRecord:
    """Resolve _dmarc.<domain> TXT via dnspython; return parsed record.

    Falls back through Google DoH if dnspython isn't available (e.g. fresh
    container without the package) — keeps the monitor working even before
    a redeploy picks up requirements.txt.
    """
    name = f"_dmarc.{domain}"
    rec = DmarcRecord(domain=domain, raw="")

    try:
        import dns.resolver  # type: ignore
        try:
            answers = dns.resolver.resolve(name, "TXT", lifetime=5)
        except Exception as e:
            rec.error = f"resolve failed: {type(e).__name__}: {e}"
            return rec
        for ans in answers:
            text = "".join(s.decode() if isinstance(s, bytes) else str(s)
                           for s in ans.strings)
            if "v=DMARC1" in text:
                rec.raw = text
                break
    except ImportError:
        # DoH fallback — Google's public resolver
        try:
            r = requests.get(
                "https://dns.google/resolve",
                params={"name": name, "type": "TXT"},
                timeout=8,
            )
            r.raise_for_status()
            body = r.json()
            for ans in body.get("Answer", []) or []:
                txt = (ans.get("data") or "").strip('"')
                if "v=DMARC1" in txt:
                    rec.raw = txt
                    break
        except Exception as e:
            rec.error = f"DoH lookup failed: {type(e).__name__}: {e}"
            return rec

    if rec.raw:
        rec.found = True
        parts = _parse_dmarc_txt(rec.raw)
        rec.p = parts.get("p")
        rec.sp = parts.get("sp")
        rec.fo = parts.get("fo")
        rec.rua = parts.get("rua")
        rec.ruf = parts.get("ruf")
    return rec


# ── Audit ─────────────────────────────────────────────────────────────────


@dataclass
class WdDmarcFinding:
    severity: str   # CRITICAL | WARN | OK
    domain: str
    summary: str
    detail: str


@dataclass
class WdDmarcAuditResult:
    as_of: str
    primary: DmarcRecord
    warmup: List[DmarcRecord] = field(default_factory=list)
    findings: List[WdDmarcFinding] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "CRITICAL" for f in self.findings)


def audit(now: Optional[_dt.date] = None) -> WdDmarcAuditResult:
    """Run the full DMARC audit. now=override for testing."""
    today = now or _dt.datetime.now(_dt.timezone.utc).date()
    result = WdDmarcAuditResult(
        as_of=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        primary=lookup_dmarc(PRIMARY_DOMAIN),
    )

    # ── Primary domain check
    pr = result.primary
    if pr.error:
        result.findings.append(WdDmarcFinding(
            severity="CRITICAL",
            domain=pr.domain,
            summary=f"Primary domain {pr.domain} DMARC lookup FAILED",
            detail=pr.error,
        ))
    elif not pr.found:
        result.findings.append(WdDmarcFinding(
            severity="CRITICAL",
            domain=pr.domain,
            summary=f"No DMARC record for primary domain {pr.domain}",
            detail="Brand + Loops transactional mail is unprotected. "
                   "Add `v=DMARC1; p=reject; sp=reject; rua=mailto:...`.",
        ))
    elif not pr.is_enforcing():
        result.findings.append(WdDmarcFinding(
            severity="CRITICAL",
            domain=pr.domain,
            summary=f"Primary domain {pr.domain} is at p={pr.p} (NOT enforcing)",
            detail=f"Per RS flag #9, primary MUST stay at p=reject (or quarantine) "
                   f"so brand + Loops mail stays protected. Current record: {pr.raw}",
        ))
    else:
        result.findings.append(WdDmarcFinding(
            severity="OK",
            domain=pr.domain,
            summary=f"Primary domain {pr.domain} enforcing (p={pr.p})",
            detail=pr.raw,
        ))

    # ── Warmup domains check
    past_grace = today > WARMUP_GRACE_END
    for d in WARMUP_DOMAINS:
        rec = lookup_dmarc(d)
        result.warmup.append(rec)
        if rec.error or not rec.found:
            result.findings.append(WdDmarcFinding(
                severity="WARN",
                domain=d,
                summary=f"Warmup domain {d} DMARC missing or unresolvable",
                detail=rec.error or "no v=DMARC1 record",
            ))
            continue
        if past_grace and not rec.is_enforcing():
            result.findings.append(WdDmarcFinding(
                severity="WARN",
                domain=d,
                summary=f"Warmup domain {d} still at p={rec.p} after warmup-grace ({WARMUP_GRACE_END})",
                detail=f"Re-tighten to p=reject (or quarantine) now. Current: {rec.raw}",
            ))
        else:
            result.findings.append(WdDmarcFinding(
                severity="OK",
                domain=d,
                summary=f"Warmup domain {d} at p={rec.p} (expected during warmup)",
                detail=f"WARMUP_GRACE_END={WARMUP_GRACE_END}. Re-tighten after this date.",
            ))

    return result


# ── Slack dispatch via avo-slack /pit-wall/dispatch ───────────────────────


def _dispatch_url() -> str:
    return (os.getenv("WD_DMARC_DISPATCH_URL") or _DISPATCH_URL_DEFAULT).strip()


def _dispatch_secret() -> str:
    return (os.getenv("PIT_WALL_DISPATCH_SECRET") or "").strip()


def _format_message(result: WdDmarcAuditResult) -> Optional[str]:
    """Render the result as a Slack message. Returns None if everything is OK
    (so the weekly cron stays quiet when nothing's wrong)."""
    critical = [f for f in result.findings if f.severity == "CRITICAL"]
    warn = [f for f in result.findings if f.severity == "WARN"]
    if not critical and not warn:
        return None

    lines: List[str] = []
    if critical:
        lines.append(":rotating_light: *WD DMARC monitor — CRITICAL*")
        for f in critical:
            lines.append(f"• `{f.domain}` — {f.summary}")
            lines.append(f"  ↳ {f.detail[:300]}")
    if warn:
        if critical:
            lines.append("")
        lines.append(":warning: *WD DMARC monitor — WARN*")
        for f in warn:
            lines.append(f"• `{f.domain}` — {f.summary}")
            lines.append(f"  ↳ {f.detail[:300]}")
    lines.append(f"\n_audit: {result.as_of}_")
    return "\n".join(lines)


def dispatch_findings(result: WdDmarcAuditResult) -> Dict[str, str]:
    """Send a Pit Wall dispatch on CRITICAL/WARN findings. No-op when clean."""
    message = _format_message(result)
    if message is None:
        return {"status": "skipped", "reason": "no findings"}
    url = _dispatch_url()
    secret = _dispatch_secret()
    if not secret:
        return {"status": "skipped", "reason": "PIT_WALL_DISPATCH_SECRET missing"}
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            json={
                "channel": "build-tech",
                "message": message,
                "posted_by": "WD DMARC monitor",
            },
            timeout=10,
        )
        if r.ok:
            return {"status": "posted", "ts": (r.json().get("ts") or "")}
        return {"status": "failed", "http": str(r.status_code), "body": r.text[:200]}
    except Exception as e:
        logger.warning("[wd-dmarc] dispatch failed: %s", e)
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


# ── Public entry points ──────────────────────────────────────────────────


def run_weekly() -> Dict[str, object]:
    """APScheduler entry — audit + dispatch. Always returns a summary dict."""
    result = audit()
    dispatch = dispatch_findings(result)
    return {
        "as_of": result.as_of,
        "primary_p": result.primary.p,
        "primary_enforcing": result.primary.is_enforcing(),
        "warmup": [{"domain": w.domain, "p": w.p} for w in result.warmup],
        "findings": [
            {"severity": f.severity, "domain": f.domain, "summary": f.summary}
            for f in result.findings
        ],
        "dispatch": dispatch,
    }


def run_now_json() -> Dict[str, object]:
    """Used by POST /admin/wd-dmarc-audit; mirrors run_weekly but always
    returns the full result, not just the summary."""
    return run_weekly()
