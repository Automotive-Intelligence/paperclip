"""services/stack_inventory.py -- the answer to "what do we actually run?"

Three times in one day someone asked whether a piece of AVO existed and got a
different answer each time: `paperclipai` was never ours, the Postal rail was ours but
had been RETIRED four days earlier, and CrewAI was quietly running in production the
whole time. None of those were memory failures. There was simply no single place that
answered the question, and 716 commits across 1,038 files is well past what anyone
holds in their head.

So this GENERATES the inventory from ground truth rather than asking a human to
maintain one. A hand-written inventory is a chore, and chores here go cold (see the
Postal re-auth ritual, abandoned exactly as it should have been). Facts come from:

  - the LIVE scheduler object   -> what is actually scheduled, not what the source says
  - the LIVE FastAPI route table-> what is actually served
  - requirements.txt           -> what we actually depend on
  - services/ docstrings       -> what each module is for, in its own words

The one thing a machine cannot derive is INTENT: what we tried, retired, or decided
against, and why. That lives in a hand-written section below a marker, and the
generator PRESERVES it verbatim on every run. Auto-facts stay fresh; human decisions
are never clobbered by a regeneration.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_PATH = "stack_inventory.md"
_CURATED_MARKER = "<!-- CURATED BELOW: hand-written, never regenerated -->"
_CT = "America/Chicago"

_DEFAULT_CURATED = f"""{_CURATED_MARKER}

## Decided against / retired (hand-written, survives regeneration)

Everything above is generated from the live system. This section is the part a machine
cannot know: what we tried, killed, or ruled out, and why. Add to it whenever a decision
is made, so nobody re-litigates it in three weeks.

| Thing | Verdict | When | Why / where the record lives |
|---|---|---|---|
| Inbound Postal/Gmail rail | RETIRED | 2026-08-16 | Owner: "didn't find value in gathering the emails." Unverified app + restricted Gmail scopes = 7-day token death; escape is CASA verification or a DWD service account. paperclip PR #288. Full detail in `infrastructure_state.md`. |
| Postal "publish to production" as the fix | DISPROVEN | 2026-08-02 | Was tried; tokens died anyway ~08-09. Publishing is not enough without verification. Do not propose again. |
| Supermetrics | DECLINED | 2026-06-26 | $177/mo aggregation over pipes we already own. Build direct. |
| Slack / AVO Slack bot | DECOMMISSIONED | 2026-07-20 | Routing moved to owned-file flags + GitHub-issue->email rail. Do not propose channels. |
| `paperclipai` (npm) | NOT OURS | 2026-08-23 | Unrelated open-source project that shares the name. Our paperclip is a private FastAPI service with no CLI. |
| Composio | NOT NOW | 2026-08-23 | Managed-auth layer over integrations we already own; would sit upstream of every credential. Free tier OK as a spike tool, never a production rail. |
"""


def _now_ct() -> str:
    try:
        import pytz
        return datetime.now(pytz.timezone(_CT)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _requirements() -> List[str]:
    """Top-level declared dependencies (name + pin), in file order."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "requirements.txt")
    out: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("-"):
                    out.append(line)
    except Exception:
        logger.warning("[inventory] could not read requirements.txt")
    return out


def _jobs(scheduler: Any) -> List[Dict[str, str]]:
    """What is ACTUALLY scheduled, read off the live scheduler (not parsed source)."""
    rows: List[Dict[str, str]] = []
    try:
        for j in scheduler.get_jobs():
            rows.append({"id": str(j.id), "name": str(getattr(j, "name", "") or ""),
                         "trigger": str(getattr(j, "trigger", ""))})
    except Exception:
        logger.warning("[inventory] scheduler unreadable")
    return sorted(rows, key=lambda r: r["id"])


def _routes(app: Any) -> Dict[str, Any]:
    """What is ACTUALLY served, read off the live FastAPI route table."""
    paths: List[str] = []
    try:
        for r in app.routes:
            p = getattr(r, "path", None)
            if p:
                paths.append(p)
    except Exception:
        logger.warning("[inventory] routes unreadable")
    groups: Dict[str, int] = {}
    for p in paths:
        top = "/" + (p.strip("/").split("/")[0] if p.strip("/") else "(root)")
        groups[top] = groups.get(top, 0) + 1
    return {"total": len(paths), "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]))}


def _services() -> List[Dict[str, str]]:
    """Each services/ module and the first line of its docstring (its own words)."""
    d = os.path.dirname(os.path.abspath(__file__))
    out: List[Dict[str, str]] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        purpose = ""
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                head = fh.read(1200)
            m = re.search(r'"""(.+?)(?:\n|""")', head, re.S)
            if m:
                purpose = re.sub(r"\s+", " ", m.group(1)).strip()
                purpose = purpose.split(" -- ", 1)[-1][:110]
        except Exception:
            pass
        out.append({"module": fn, "purpose": purpose})
    return out


def _preserve_curated(existing: str) -> str:
    """Keep the hand-written section verbatim. This is the whole reason regeneration is
    safe: auto-facts refresh, human decisions are never overwritten."""
    if existing and _CURATED_MARKER in existing:
        return existing[existing.index(_CURATED_MARKER):]
    return _DEFAULT_CURATED


def render(app: Any, scheduler: Any, existing: str = "") -> str:
    deps = _requirements()
    jobs = _jobs(scheduler)
    routes = _routes(app)
    svcs = _services()

    dep_lines = "\n".join(f"- `{d}`" for d in deps[:60])
    if len(deps) > 60:
        dep_lines += f"\n- ...and {len(deps) - 60} more"

    job_lines = "\n".join(
        f"| `{j['id']}` | {j['name'][:52]} | `{j['trigger'][:58]}` |" for j in jobs)
    group_lines = "\n".join(f"- `{g}` ({n} routes)" for g, n in routes["groups"].items())
    svc_lines = "\n".join(
        f"| `{s['module']}` | {s['purpose']} |" for s in svcs if s["purpose"])

    return f"""# AVO Stack Inventory

<!-- GENERATED. Everything above the curated marker is rebuilt from the LIVE system on
     each run: the running scheduler, the served route table, requirements.txt, and the
     services/ docstrings. Do not hand-edit this part; edits are overwritten. Add human
     decisions BELOW the marker instead. -->

**Generated:** {_now_ct()} · **Source:** live paperclip process (not source parsing, not memory)

> Why this file exists: on 2026-08-23, three questions about whether a piece of AVO
> existed got three different answers (never ours / ours but retired / running in
> production right now). The system is past the size any one person can hold. Check here
> before proposing, building, or declaring something missing.

## Runtime

- **Repo:** `Automotive-Intelligence/paperclip` (private) — FastAPI service, deployed on Railway
- **Not a CLI.** There is no `paperclip` command. The unrelated npm package `paperclipai` is a different product.
- **Scheduled jobs live now:** {len(jobs)}
- **Routes served:** {routes['total']}
- **Declared dependencies:** {len(deps)}

## Scheduled jobs (from the running scheduler)

| id | name | trigger |
|---|---|---|
{job_lines}

## Route surface

{group_lines}

## Services

| module | purpose |
|---|---|
{svc_lines}

## Dependencies

{dep_lines}

{_preserve_curated(existing)}"""


def build(app: Any, scheduler: Any, *, commit: bool = True) -> Dict[str, Any]:
    """Regenerate the inventory and commit it to avo-telemetry. Idempotent: if nothing
    but the timestamp changed, skip the commit so the file's history stays meaningful."""
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
             or os.getenv("SLIPSTREAM_GH_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "error": "no GitHub token; cannot commit inventory"}

    result: Dict[str, Any] = {}

    def _transform(existing: str) -> Optional[str]:
        new = render(app, scheduler, existing or "")
        result["chars"] = len(new)
        if existing:
            # Ignore the timestamp line when deciding whether anything really changed.
            strip = lambda t: re.sub(r"\*\*Generated:\*\*.*", "", t)
            if strip(existing).strip() == strip(new).strip():
                return None
        return new

    if not commit:
        return {"ok": True, "preview": render(app, scheduler, ""), "committed": False}

    from services.avo_state_commit import update_state
    out = update_state(STATE_PATH, _transform, "inventory: regenerate from live system", token)
    return {"ok": True, "committed": bool(out.get("committed")),
            "skipped": bool(out.get("skipped")), "chars": result.get("chars")}
