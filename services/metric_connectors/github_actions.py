"""github_actions connector — B&T deploy_frequency KPI.

Counts GitHub Actions workflow runs across tracked repos in the last 7 days.
"Deploy" = successful production-deploy workflow run (workflow file convention:
.github/workflows/deploy.yml or any workflow whose name contains "deploy").

Uses GitHub REST API via the GITHUB_TOKEN_TELEMETRY scope already wired for
cockpit_bridge. No new auth surface.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List

import requests

from services.metric_connectors.types import KPIReading

logger = logging.getLogger(__name__)

# Repos whose deploys count toward the org's deploy frequency. Extend here as
# new production repos land. Keep tight — non-prod repos pollute the signal.
TRACKED_REPOS = [
    "Automotive-Intelligence/paperclip",
    "Automotive-Intelligence/avo-telemetry",
    "Automotive-Intelligence/avi-website",
    "Automotive-Intelligence/avo-cockpit",
    "Automotive-Intelligence/buildagentempire",
    "Automotive-Intelligence/worshipdigital",
]

_API_BASE = "https://api.github.com"
_REQUEST_TIMEOUT = 12
_WINDOW_DAYS = 7


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name != "deploy_frequency":
        raise ValueError(f"github_actions: unsupported kpi {name!r}")
    return [_deploy_frequency()]


def _headers() -> dict:
    token = (os.getenv("GITHUB_TOKEN_TELEMETRY") or os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("github_actions: GITHUB_TOKEN_TELEMETRY / GITHUB_TOKEN not set")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _deploy_frequency() -> KPIReading:
    headers = _headers()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    total = 0
    per_repo: dict = {}
    errors: list = []

    for repo in TRACKED_REPOS:
        try:
            # status=success + per_page=100 (sufficient for 7 days at most repos)
            r = requests.get(
                f"{_API_BASE}/repos/{repo}/actions/runs",
                headers=headers,
                params={"status": "success", "created": f">={cutoff_str}", "per_page": 100},
                timeout=_REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            runs = (r.json() or {}).get("workflow_runs") or []
            # Filter to deploy-shaped workflows. A repo with 50 lint+test runs
            # shouldn't dwarf a repo with 5 actual deploys.
            deploys = [
                run for run in runs
                if "deploy" in (run.get("name") or "").lower()
                or "deploy" in (run.get("path") or "").lower()
                or (run.get("event") or "") in ("push",) and run.get("head_branch") in ("main", "master")
            ]
            per_repo[repo] = len(deploys)
            total += len(deploys)
        except requests.HTTPError as e:
            # 404 just means we don't have access — track but don't fail loud
            per_repo[repo] = None
            errors.append({"repo": repo, "error": str(e)[:200]})
            logger.info("[github_actions] %s — %s", repo, e)
        except Exception as e:
            per_repo[repo] = None
            errors.append({"repo": repo, "error": str(e)[:200]})

    if total == 0 and errors and len(errors) == len(TRACKED_REPOS):
        # Every repo failed — that's connector_down territory, not no_data
        return KPIReading(
            persona="bt",
            kpi_name="deploy_frequency",
            status="connector_down",
            error_detail=f"all {len(TRACKED_REPOS)} repos errored; first: {errors[0].get('error', '')[:200]}",
            raw_payload={"errors": errors},
        )

    return KPIReading(
        persona="bt",
        kpi_name="deploy_frequency",
        value_numeric=float(total),
        unit="deploys/week",
        raw_payload={
            "window_days": _WINDOW_DAYS,
            "per_repo": per_repo,
            "repos_tracked": len(TRACKED_REPOS),
            "repos_with_errors": len(errors),
        },
    )
