"""services/infrastructure_sweep.py — CTO / Infrastructure daily sweep.

Daily 7:30 AM CDT sweep of the org-tech surface. Findings get written into
the Infrastructure persona's telemetry file (`infrastructure_state.md` in
avo-telemetry) so other personas + the morning briefing can read them.

v1 checks (each returns a list of findings):
  1. Domain SSL certificate expiry — catches "expired cert" outages before
     they happen
  2. Agent run anomalies — runs/24h vs 7d median; flags silent failures
     (drop to 0) and runaway loops (spike to 2x+)
  3. Recent error patterns — last 24h agent_logs.content containing
     error/exception/failed; surfaces silent platform issues

Each finding has a severity (info/warn/critical). Overall sweep status:
  - green: zero warn/critical findings
  - yellow: any warn findings
  - red:    any critical findings

The "Active items" section of infrastructure_state.md is auto-managed by
this sweep; all other sections (Waiting on, Recently closed, Flags, Scope
reference, Standing duties) are preserved verbatim.

Reuses cockpit_bridge's GitHub client (same GITHUB_TOKEN_TELEMETRY + repo).
"""

import logging
import os
import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional, Tuple

from services.cockpit_bridge import (
    BridgeConfig,
    get_bridge_config,
    _get_file,
    _put_file,
)
from services.database import fetch_all

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────────────────────────────

# v1 — hardcoded domain inventory. v2 reads from a config table / env var.
MONITORED_DOMAINS = [
    "theaiphoneguy.ai",
    "automotiveintelligence.io",
    "calling.digital",
    "bookd.cx",
    "buildagentempire.com",
    "worshipdigital.co",            # WD root (CD rebrand, live 2026-06-11)
    "crm.worshipdigital.co",        # Twenty workspace — WD client SoT (Attio replacement)
    "bookd.twenty.com",             # Twenty workspace — Book'd client SoT
    "portal.worshipdigital.co",     # Chatwoot WD client portal (pre-deploy — graceful unreachable until live)
]

# Critical app surfaces that need HTTP-level liveness (not just SSL).
# Twenty workspaces serve a UI at root; we just need a 2xx/3xx to know the install is up.
# Chatwoot is here pre-deploy — until it's live, the check just reports "unreachable" as a non-flagging info.
MONITORED_HEALTH_URLS = [
    ("crm.worshipdigital.co",    "https://crm.worshipdigital.co/",    True),   # required=True → fail loud if down
    ("bookd.twenty.com",         "https://bookd.twenty.com/",         True),
    ("portal.worshipdigital.co", "https://portal.worshipdigital.co/", False),  # required=False → silent until deployed
]

# Browser UA spoof — Twenty's Cloudflare blocks default urllib/requests UA with 403.
# Same gotcha documented in reference_crms memory.
HEALTH_CHECK_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
HEALTH_CHECK_TIMEOUT = 8

SSL_EXPIRY_WARN_DAYS = 30
SSL_EXPIRY_CRITICAL_DAYS = 7

# Anomaly detection thresholds for agent run counts
AGENT_RUN_SPIKE_RATIO = 3.0       # runs_24h / median_7d > this  → flag
AGENT_RUN_DROP_MIN_BASELINE = 4   # only flag drops on agents with >= this median
AGENT_RUN_DROP_THRESHOLD = 0.25   # runs_24h / median_7d < this  → flag

ERROR_KEYWORDS = ("error", "exception", "failed", "traceback", "404", "500", "401", "403")
ERROR_FINDING_THRESHOLD = 3       # need this many error log lines to count as a finding

STATE_FILE = "infrastructure_state.md"
ENV_SNAPSHOT_FILE = "infrastructure_env_snapshot.json"
VERCEL_SNAPSHOT_FILE = "infrastructure_vercel_inventory.json"

# Zombie thresholds — projects with no deploy this old are candidate dead weight
VERCEL_STALE_DAYS = 60        # info-severity flag — review for retirement
VERCEL_DORMANT_DAYS = 180     # warn-severity flag — almost certainly dead weight

# Env vars that come from the runtime/platform and aren't worth tracking as
# "the org's wired secrets." Filtered out of drift snapshots.
PLATFORM_ENV_PREFIXES = (
    "RAILWAY_", "NIXPACKS_", "PIP_", "PYTHON", "PATH", "PWD", "HOME",
    "LANG", "LC_", "TERM", "SHELL", "USER", "HOSTNAME", "OLDPWD",
    "PORT", "VIRTUAL_ENV", "_", "SHLVL", "DEBIAN_", "GPG_", "SSH_",
)
PLATFORM_ENV_EXACT = {"PORT", "PWD", "HOME", "USER", "HOSTNAME", "PATH", "LANG"}


@dataclass
class Finding:
    check: str             # which check produced this ("domain_ssl", "agent_runs", "errors")
    severity: str          # "info" | "warn" | "critical"
    title: str             # one-line headline
    detail: str = ""       # optional multi-line context

    def to_md_line(self) -> str:
        icon = {"info": "ℹ️", "warn": "⚠️", "critical": "🚨"}.get(self.severity, "·")
        line = f"- {icon} **[{self.check}]** {self.title}"
        if self.detail:
            line += f"\n  {self.detail.strip().replace(chr(10), chr(10) + '  ')}"
        return line


@dataclass
class SweepResult:
    findings: List[Finding] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def status(self) -> str:
        sev = {f.severity for f in self.findings}
        if "critical" in sev:
            return "red"
        if "warn" in sev:
            return "yellow"
        return "green"

    @property
    def status_icon(self) -> str:
        return {"red": "🔴", "yellow": "🟡", "green": "🟢"}[self.status]


# ── Check 1: domain SSL expiry ──────────────────────────────────────────────

def _ssl_cert_expiry(hostname: str, port: int = 443, timeout: int = 6) -> Optional[datetime]:
    """Return the SSL cert's `notAfter` as a tz-aware datetime, or None on failure.

    For expiry-watching we only need the cert's notAfter field — we don't need
    to validate the trust chain. Falls back to a no-verify connection when the
    default context can't validate (e.g. missing CA bundle in some envs); the
    cert content itself is still authoritative for the expiry date.
    """
    def _read(ctx_factory) -> Optional[Dict]:
        try:
            ctx = ctx_factory()
            with socket.create_connection((hostname, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    return ssock.getpeercert()
        except ssl.SSLCertVerificationError:
            return None
        except Exception as e:
            logger.warning("[infra_sweep] SSL connect %s failed: %s", hostname, e)
            return None

    cert = _read(ssl.create_default_context)
    if cert is None:
        # Fall back: bypass verification (we only care about notAfter)
        def _unverified_ctx() -> ssl.SSLContext:
            c = ssl.create_default_context()
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            return c
        # CERT_NONE doesn't populate getpeercert() — need binary form
        try:
            with socket.create_connection((hostname, port), timeout=timeout) as sock:
                with _unverified_ctx().wrap_socket(sock, server_hostname=hostname) as ssock:
                    der = ssock.getpeercert(binary_form=True)
            if not der:
                return None
            # Decode via the standard library — no external deps
            import datetime as _dt
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                cert_obj = x509.load_der_x509_certificate(der, default_backend())
                return cert_obj.not_valid_after.replace(tzinfo=timezone.utc)
            except ImportError:
                # cryptography not installed — give up; production has it
                logger.warning("[infra_sweep] cryptography not available for %s fallback", hostname)
                return None
        except Exception as e:
            logger.warning("[infra_sweep] SSL fallback %s failed: %s", hostname, e)
            return None

    not_after = cert.get("notAfter") if cert else None
    if not not_after:
        return None
    return datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def check_domain_ssl() -> List[Finding]:
    findings: List[Finding] = []
    now = datetime.now(timezone.utc)
    for host in MONITORED_DOMAINS:
        expiry = _ssl_cert_expiry(host)
        if expiry is None:
            findings.append(Finding(
                check="domain_ssl",
                severity="warn",
                title=f"{host} — could not read SSL certificate",
                detail="Possible: DNS not resolving, port 443 closed, handshake error.",
            ))
            continue
        days_left = (expiry - now).days
        if days_left <= SSL_EXPIRY_CRITICAL_DAYS:
            findings.append(Finding(
                check="domain_ssl",
                severity="critical",
                title=f"{host} SSL cert expires in {days_left}d ({expiry.date()})",
                detail="Renewal needed NOW — outage risk imminent.",
            ))
        elif days_left <= SSL_EXPIRY_WARN_DAYS:
            findings.append(Finding(
                check="domain_ssl",
                severity="warn",
                title=f"{host} SSL cert expires in {days_left}d ({expiry.date()})",
                detail=f"Schedule renewal within {SSL_EXPIRY_WARN_DAYS - days_left}d.",
            ))
    return findings


# ── Check 2: agent run anomalies ────────────────────────────────────────────

def check_agent_run_anomalies() -> List[Finding]:
    findings: List[Finding] = []
    try:
        rows = fetch_all(
            """
            WITH today AS (
                SELECT agent_name, COUNT(*) AS runs_24h
                FROM agent_logs
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY agent_name
            ),
            past7 AS (
                SELECT agent_name,
                       date_trunc('day', created_at) AS day,
                       COUNT(*) AS runs
                FROM agent_logs
                WHERE created_at >= NOW() - INTERVAL '8 days'
                  AND created_at <  NOW() - INTERVAL '24 hours'
                GROUP BY agent_name, date_trunc('day', created_at)
            ),
            baseline AS (
                SELECT agent_name, percentile_cont(0.5) WITHIN GROUP (ORDER BY runs) AS median_7d
                FROM past7 GROUP BY agent_name
            )
            SELECT COALESCE(t.agent_name, b.agent_name) AS agent_name,
                   COALESCE(t.runs_24h, 0) AS runs_24h,
                   COALESCE(b.median_7d, 0)::float AS median_7d
            FROM today t
            FULL OUTER JOIN baseline b USING (agent_name)
            """
        )
    except Exception as e:
        logger.warning("[infra_sweep] agent_run query failed: %s", e)
        return [Finding(
            check="agent_runs",
            severity="warn",
            title="agent_logs query failed",
            detail=str(e)[:200],
        )]

    for agent_name, runs_24h, median_7d in rows:
        if not agent_name:
            continue
        if median_7d and median_7d >= AGENT_RUN_DROP_MIN_BASELINE:
            ratio = runs_24h / median_7d if median_7d > 0 else 0
            if ratio < AGENT_RUN_DROP_THRESHOLD:
                findings.append(Finding(
                    check="agent_runs",
                    severity="warn" if runs_24h > 0 else "critical",
                    title=f"{agent_name}: {runs_24h} runs / 24h (7d median {median_7d:.0f}) — silent failure?",
                    detail=f"ratio {ratio:.2f} vs threshold {AGENT_RUN_DROP_THRESHOLD}",
                ))
        if median_7d > 0:
            ratio = runs_24h / median_7d if median_7d > 0 else 0
            if ratio >= AGENT_RUN_SPIKE_RATIO:
                findings.append(Finding(
                    check="agent_runs",
                    severity="warn",
                    title=f"{agent_name}: {runs_24h} runs / 24h (7d median {median_7d:.0f}) — runaway?",
                    detail=f"ratio {ratio:.2f}x baseline — possible loop / cost burn",
                ))
    return findings


# ── Check 3: recent error patterns in agent_logs ────────────────────────────

def check_vercel_inventory() -> List[Finding]:
    """List Vercel projects + last-deploy age, flag candidate zombies.

    Surfaces:
      - Project inventory (count, names, last deploy date per project)
      - Stale projects (no deploy >60d) — info-severity
      - Dormant projects (no deploy >180d) — warn-severity, candidate kill
      - Erroring projects (READY != latest deploy state) — warn

    Snapshot pushed to avo-telemetry/infrastructure_vercel_inventory.json.

    Graceful skip when VERCEL_API_TOKEN not set; emits one info-severity
    finding pointing at the configuration ask.
    """
    import json as _json
    import requests as _requests

    token = (os.environ.get("VERCEL_API_TOKEN") or os.environ.get("VERCEL_TOKEN") or "").strip()
    team_id = (os.environ.get("VERCEL_TEAM_ID") or "").strip()

    if not token:
        return [Finding(
            check="vercel_inventory",
            severity="info",
            title="Vercel inventory check skipped — VERCEL_API_TOKEN not configured",
            detail="Set VERCEL_API_TOKEN on Railway (Vercel → Account Settings → Tokens) to enable zombie-project surfacing. Optional: VERCEL_TEAM_ID if using a team account.",
        )]

    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 100}
    if team_id:
        params["teamId"] = team_id

    try:
        r = _requests.get("https://api.vercel.com/v9/projects",
                          headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            return [Finding(
                check="vercel_inventory",
                severity="warn",
                title=f"Vercel API returned HTTP {r.status_code}",
                detail=f"body: {r.text[:200]} — token may be expired or scoped wrong",
            )]
        projects = (r.json() or {}).get("projects", [])
    except Exception as e:
        logger.warning("[infra_sweep] Vercel inventory fetch failed: %s", e)
        return [Finding(
            check="vercel_inventory",
            severity="warn",
            title="Vercel inventory fetch errored",
            detail=str(e)[:200],
        )]

    findings: List[Finding] = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale = []
    dormant = []
    erroring = []
    inventory: List[Dict[str, Any]] = []

    for p in projects:
        name = p.get("name")
        latest = (p.get("latestDeployments") or [{}])[0]
        ts = latest.get("createdAt") or p.get("createdAt") or 0
        days_old = int((now_ms - int(ts)) / (1000 * 60 * 60 * 24)) if ts else None
        state = latest.get("readyState") or latest.get("state")
        framework = p.get("framework")
        prod_domain = next(
            (d.get("name") for d in (p.get("targets", {}).get("production", {}).get("alias", []) or []) if d.get("name")),
            None,
        )

        inventory.append({
            "name": name,
            "framework": framework,
            "last_deploy_days_ago": days_old,
            "last_state": state,
            "production_alias": prod_domain,
        })

        if state and state not in ("READY", "BUILDING", "QUEUED", "INITIALIZING"):
            erroring.append(f"{name} ({state})")
        if days_old is not None:
            if days_old >= VERCEL_DORMANT_DAYS:
                dormant.append(f"{name} ({days_old}d)")
            elif days_old >= VERCEL_STALE_DAYS:
                stale.append(f"{name} ({days_old}d)")

    findings.append(Finding(
        check="vercel_inventory",
        severity="info",
        title=f"Vercel projects under management: {len(projects)}",
        detail="Inventory snapshot pushed to avo-telemetry/infrastructure_vercel_inventory.json",
    ))
    if dormant:
        findings.append(Finding(
            check="vercel_inventory",
            severity="warn",
            title=f"{len(dormant)} dormant Vercel project(s) — no deploy >{VERCEL_DORMANT_DAYS}d",
            detail="Candidate kills: " + ", ".join(dormant[:10]) + (" …" if len(dormant) > 10 else ""),
        ))
    if stale:
        findings.append(Finding(
            check="vercel_inventory",
            severity="info",
            title=f"{len(stale)} stale Vercel project(s) — no deploy {VERCEL_STALE_DAYS}-{VERCEL_DORMANT_DAYS}d",
            detail="Review for retirement: " + ", ".join(stale[:10]) + (" …" if len(stale) > 10 else ""),
        ))
    if erroring:
        findings.append(Finding(
            check="vercel_inventory",
            severity="warn",
            title=f"{len(erroring)} Vercel project(s) with last deploy in non-ready state",
            detail=", ".join(erroring[:10]) + (" …" if len(erroring) > 10 else ""),
        ))

    # Push inventory snapshot to telemetry
    try:
        from services.cockpit_bridge import get_bridge_config, _get_file, _put_file
        cfg = get_bridge_config()
        if cfg:
            snapshot = _json.dumps({
                "snapshot_taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "project_count": len(projects),
                "stale_count": len(stale),
                "dormant_count": len(dormant),
                "projects": inventory,
            }, indent=2) + "\n"
            existing = _get_file(cfg, VERCEL_SNAPSHOT_FILE)
            sha = existing[1] if existing else ""
            _put_file(cfg, VERCEL_SNAPSHOT_FILE, snapshot, sha,
                message=f"chore(infrastructure): vercel inventory snapshot — {len(projects)} projects, {len(dormant)} dormant")
    except Exception as e:
        logger.warning("[infra_sweep] Vercel snapshot push failed: %s", e)

    return findings


def _current_org_env_var_names() -> List[str]:
    """Return sorted env-var NAMES (never values) excluding platform-injected ones.

    Hard rule: this function NEVER returns or logs values. The drift detector
    operates on names only — secrets must not enter the snapshot, logs, or
    the avo-telemetry repo.
    """
    names: List[str] = []
    for name in os.environ.keys():
        if name in PLATFORM_ENV_EXACT:
            continue
        if any(name.startswith(p) for p in PLATFORM_ENV_PREFIXES):
            continue
        names.append(name)
    return sorted(names)


def check_env_var_drift() -> List[Finding]:
    """Compare current env-var NAME set to the prior snapshot in avo-telemetry.

    Flags:
      - added:   vars present now that weren't before (could be new wiring OR
                 leaked secret name; warn so a human reviews)
      - removed: vars gone that were there before (could be intentional retirement
                 OR accidental config wipe; warn either way — Build & Tech should
                 confirm intent in the cleanup queue when an expected removal lands)

    First run with no prior snapshot is a no-op (establishes baseline). Snapshot
    is pushed back to avo-telemetry on every successful diff so the baseline
    moves forward.
    """
    import json as _json
    from services.cockpit_bridge import get_bridge_config, _get_file, _put_file
    findings: List[Finding] = []
    current = _current_org_env_var_names()

    cfg = get_bridge_config()
    if not cfg:
        logger.warning("[infra_sweep] env drift: bridge config missing — skip")
        return [Finding(
            check="env_drift",
            severity="warn",
            title="env-var drift check skipped — telemetry GitHub access not configured",
            detail="GITHUB_TOKEN_TELEMETRY / TELEMETRY_REPO not set",
        )]

    file_result = _get_file(cfg, ENV_SNAPSHOT_FILE)
    prior: Optional[Dict] = None
    sha: Optional[str] = None
    if file_result:
        content, sha = file_result
        try:
            prior = _json.loads(content)
        except Exception as e:
            logger.warning("[infra_sweep] prior env snapshot parse failed: %s", e)
            prior = None

    if not prior or not isinstance(prior.get("names"), list):
        # First run — establish baseline silently, no findings
        baseline = _json.dumps(
            {
                "snapshot_taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "count": len(current),
                "names": current,
            },
            indent=2,
        ) + "\n"
        if sha is not None:
            _put_file(cfg, ENV_SNAPSHOT_FILE, baseline, sha,
                message="chore(infrastructure): env snapshot baseline (first run)")
        else:
            # Create the file (no SHA when it doesn't exist yet)
            _put_file(cfg, ENV_SNAPSHOT_FILE, baseline, "",
                message="chore(infrastructure): env snapshot baseline (created)")
        return [Finding(
            check="env_drift",
            severity="info",
            title=f"env-var baseline established: {len(current)} tracked names",
            detail="First run — no drift to report. Future sweeps will diff against this snapshot.",
        )]

    prior_set = set(prior["names"])
    current_set = set(current)
    added = sorted(current_set - prior_set)
    removed = sorted(prior_set - current_set)

    if added:
        findings.append(Finding(
            check="env_drift",
            severity="warn",
            title=f"env-var drift: {len(added)} added since last sweep",
            detail="Added: " + ", ".join(added[:15]) + (" …" if len(added) > 15 else ""),
        ))
    if removed:
        findings.append(Finding(
            check="env_drift",
            severity="warn",
            title=f"env-var drift: {len(removed)} removed since last sweep",
            detail="Removed: " + ", ".join(removed[:15]) + (" …" if len(removed) > 15 else ""),
        ))

    # Update snapshot only if it changed — keep telemetry commit history clean
    if added or removed:
        updated = _json.dumps(
            {
                "snapshot_taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "count": len(current),
                "names": current,
                "prior_count": len(prior_set),
                "added_since_prior": added,
                "removed_since_prior": removed,
            },
            indent=2,
        ) + "\n"
        try:
            _put_file(cfg, ENV_SNAPSHOT_FILE, updated, sha or "",
                message=f"chore(infrastructure): env snapshot update — +{len(added)} -{len(removed)}")
        except Exception as e:
            logger.warning("[infra_sweep] env snapshot push failed: %s", e)

    return findings


def check_app_health() -> List[Finding]:
    """HTTP liveness check for the critical app surfaces.

    Each entry is (label, url, required). When required=True a non-2xx/3xx or
    transport error becomes a critical finding. When required=False (pre-deploy
    placeholders like Chatwoot before launch), unreachable is silent.

    Browser User-Agent spoof is required for Twenty workspaces — default Python
    UA gets 403'd by Cloudflare (documented in reference_crms memory).
    """
    import requests as _requests
    findings: List[Finding] = []
    for label, url, required in MONITORED_HEALTH_URLS:
        try:
            r = _requests.get(
                url,
                timeout=HEALTH_CHECK_TIMEOUT,
                headers={"User-Agent": HEALTH_CHECK_UA},
                allow_redirects=True,
            )
            if 200 <= r.status_code < 400:
                continue  # healthy, no finding
            sev = "critical" if required else "info"
            findings.append(Finding(
                check="app_health",
                severity=sev,
                title=f"{label} returned HTTP {r.status_code}",
                detail=f"URL: {url} — {'required surface' if required else 'optional/pre-deploy'}",
            ))
        except _requests.RequestException as e:
            if not required:
                continue  # pre-deploy, silent
            findings.append(Finding(
                check="app_health",
                severity="critical",
                title=f"{label} unreachable",
                detail=f"URL: {url} — {type(e).__name__}: {str(e)[:120]}",
            ))
    return findings


def check_recent_errors() -> List[Finding]:
    findings: List[Finding] = []
    try:
        like_clause = " OR ".join("LOWER(content) LIKE %s" for _ in ERROR_KEYWORDS)
        params: Tuple[str, ...] = tuple(f"%{kw}%" for kw in ERROR_KEYWORDS)
        rows = fetch_all(
            f"""
            SELECT agent_name, COUNT(*) AS hits
            FROM agent_logs
            WHERE created_at >= NOW() - INTERVAL '24 hours'
              AND ({like_clause})
            GROUP BY agent_name
            HAVING COUNT(*) >= %s
            ORDER BY hits DESC
            """,
            params + (ERROR_FINDING_THRESHOLD,),
        )
    except Exception as e:
        logger.warning("[infra_sweep] error_pattern query failed: %s", e)
        return [Finding(
            check="errors",
            severity="warn",
            title="error pattern query failed",
            detail=str(e)[:200],
        )]

    for agent_name, hits in rows:
        sev = "critical" if hits >= 20 else "warn"
        findings.append(Finding(
            check="errors",
            severity=sev,
            title=f"{agent_name}: {hits} error-pattern hits in last 24h",
            detail=f"keywords matched: any of {', '.join(ERROR_KEYWORDS)}",
        ))
    return findings


# ── Markdown composer + state-file update ───────────────────────────────────

ACTIVE_SECTION_RE = re.compile(
    r"(## Active items\s*\n)(.*?)(?=\n## )",
    re.DOTALL,
)


def compose_active_section(result: SweepResult) -> str:
    """Build the markdown body of the 'Active items' section."""
    head = f"## Active items\n\n**Status:** {result.status_icon} {result.status}  · Last sweep: {result.finished_at} · {len(result.findings)} findings\n\n"
    if not result.findings:
        return head + "(no findings — all checks clean)\n"
    by_sev = {"critical": [], "warn": [], "info": []}
    for f in result.findings:
        by_sev.setdefault(f.severity, []).append(f)
    lines: List[str] = []
    for sev_label, label in [("critical", "🚨 Critical"), ("warn", "⚠️ Warn"), ("info", "ℹ️ Info")]:
        if by_sev.get(sev_label):
            lines.append(f"\n### {label}\n")
            lines.extend(f.to_md_line() for f in by_sev[sev_label])
    return head + "\n".join(lines) + "\n"


def update_state_file(result: SweepResult) -> bool:
    """Replace just the 'Active items' section in infrastructure_state.md.

    Other sections (Waiting on, Recently closed, Flags, Scope reference,
    Standing duties) are preserved verbatim.
    """
    cfg = get_bridge_config()
    if not cfg:
        logger.warning("[infra_sweep] GITHUB_TOKEN_TELEMETRY / TELEMETRY_REPO not set — skipping push")
        return False

    file_result = _get_file(cfg, STATE_FILE)
    if not file_result:
        logger.warning("[infra_sweep] %s not found in telemetry repo — was infrastructure persona scaffolded?", STATE_FILE)
        return False
    content, sha = file_result

    new_section = compose_active_section(result)
    # Replace ## Active items ... up to next ## section, leaving the rest intact.
    if ACTIVE_SECTION_RE.search(content):
        new_content = ACTIVE_SECTION_RE.sub(new_section + "\n", content, count=1)
    else:
        logger.warning("[infra_sweep] could not find '## Active items' section — falling back to no-op")
        return False

    # Update "Last updated" header in-place
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_content = re.sub(
        r"^\*\*Last updated:\*\*.*$",
        f"**Last updated:** {today}",
        new_content,
        count=1,
        flags=re.MULTILINE,
    )
    new_content = re.sub(
        r"^\*\*Status:\*\*.*$",
        f"**Status:** {result.status}",
        new_content,
        count=1,
        flags=re.MULTILINE,
    )

    ok = _put_file(
        cfg,
        STATE_FILE,
        new_content,
        sha,
        message=f"chore(infrastructure): cto_daily_sweep — {result.status} {len(result.findings)} findings",
    )
    if ok:
        logger.info("[infra_sweep] pushed update to %s (%d findings, status=%s)", STATE_FILE, len(result.findings), result.status)
    return ok


# ── Public entry points ─────────────────────────────────────────────────────

def run_sweep(*, push: bool = True) -> SweepResult:
    """Run all checks, optionally push results to telemetry. Returns the SweepResult."""
    started = datetime.now(timezone.utc)
    result = SweepResult(started_at=started.isoformat(timespec="seconds"))
    try:
        result.findings.extend(check_domain_ssl())
    except Exception as e:
        logger.exception("[infra_sweep] check_domain_ssl errored: %s", e)
    try:
        result.findings.extend(check_agent_run_anomalies())
    except Exception as e:
        logger.exception("[infra_sweep] check_agent_run_anomalies errored: %s", e)
    try:
        result.findings.extend(check_recent_errors())
    except Exception as e:
        logger.exception("[infra_sweep] check_recent_errors errored: %s", e)
    try:
        result.findings.extend(check_app_health())
    except Exception as e:
        logger.exception("[infra_sweep] check_app_health errored: %s", e)
    try:
        result.findings.extend(check_env_var_drift())
    except Exception as e:
        logger.exception("[infra_sweep] check_env_var_drift errored: %s", e)
    try:
        result.findings.extend(check_vercel_inventory())
    except Exception as e:
        logger.exception("[infra_sweep] check_vercel_inventory errored: %s", e)
    result.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if push:
        try:
            update_state_file(result)
        except Exception as e:
            logger.exception("[infra_sweep] update_state_file errored: %s", e)
    return result


def cto_daily_sweep() -> Dict[str, object]:
    """APScheduler entry point. Wraps run_sweep, returns a summary dict."""
    result = run_sweep(push=True)
    return {
        "status": result.status,
        "findings": len(result.findings),
        "by_severity": {
            sev: sum(1 for f in result.findings if f.severity == sev)
            for sev in ("critical", "warn", "info")
        },
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }


# ── Smoke test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("\n### INFRASTRUCTURE SWEEP (smoke test, no push) ###\n")
    r = run_sweep(push=False)
    print(f"Status: {r.status_icon} {r.status}")
    print(f"Findings: {len(r.findings)}")
    print(f"Range: {r.started_at} -> {r.finished_at}\n")
    print(compose_active_section(r))
