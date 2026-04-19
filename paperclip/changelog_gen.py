# AVO — AI Business Operating System
# Changelog Generator
# Built live for Agent Empire Skool community
# Salesdroid — April 2026
# North Star: $15,000 MRR

"""Weekly changelog generator.

Schedule: Every Friday 5pm CST
Output: CHANGELOG_WEEK_[N]_2026.md in repo root
Contents: contacts enrolled, replies, hot leads, revenue impact, bugs fixed, next week
Posts to Agent Empire Skool every Saturday — this IS the build-in-public content.
"""

import os
import subprocess
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
LOGS_DIR = os.path.join(REPO_ROOT, "logs")


def _get_week_number() -> int:
    """Get ISO week number."""
    return datetime.now().isocalendar()[1]


def _iso_week_bounds(week: int, year: int) -> tuple[str, str]:
    """Return (monday_iso, next_monday_iso) for the given ISO week."""
    monday = datetime.fromisocalendar(year, week, 1)
    next_monday = monday + timedelta(days=7)
    return monday.strftime("%Y-%m-%d"), next_monday.strftime("%Y-%m-%d")


def _read_log_tail(filename: str, lines: int = 100) -> list:
    """Read last N lines from a log file."""
    path = os.path.join(LOGS_DIR, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            all_lines = f.readlines()
            return all_lines[-lines:]
    except Exception:
        return []


def _count_log_events(filename: str, keyword: str, since_days: int = 7) -> int:
    """Count events matching keyword in log file from last N days."""
    cutoff = datetime.now() - timedelta(days=since_days)
    lines = _read_log_tail(filename, 500)
    count = 0
    for line in lines:
        if keyword.lower() in line.lower():
            try:
                ts_str = line.split("|")[0].strip()
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
                if ts >= cutoff:
                    count += 1
            except (ValueError, IndexError):
                count += 1
    return count


def _generate_talking_points(week: int, year: int, git: dict, by_biz: dict) -> str:
    """Use the configured LLM to produce 3 narrative bullets for YouTube recap.

    Gracefully returns empty string if no API key or LLM call fails — the
    changelog still renders without this section.
    """
    api_key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        return ""

    commits_preview = "\n".join(git.get("all_commits", [])[:60])
    biz_lines = "\n".join(f"- {b}: {c} prospects created" for b, c in by_biz.items()) or "- No CRM activity"
    prompt = f"""You are writing a weekly build-in-public recap for Michael, founder of AVO (AI Business Operating System).

Context for Week {week} of {year}:
- {git.get('total_commits', 0)} commits shipped
- {len(git.get('features_added', []))} features added
- {len(git.get('bugs_fixed', []))} bugs fixed

Commits this week:
{commits_preview}

Pipeline activity:
{biz_lines}

Your job: write exactly 3 bullet points for Michael's Sunday YouTube recap. Each bullet is ONE short sentence (15 words max). Focus on the narrative — what story does this week tell? What shipped that a customer or community member would care about? Write in plain language, no jargon, no emojis. Just the bullets, no preamble."""

    try:
        model = os.getenv("LLM_MODEL", "openrouter/google/gemini-2.5-flash")
        from litellm import completion
        resp = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key,
            max_tokens=300,
            temperature=0.6,
        )
        text = resp.choices[0].message.content.strip()
        return text
    except Exception as e:
        logger.warning(f"[Changelog] Talking points generation failed: {e}")
        return ""


def _render_pipeline_section(by_business: dict) -> str:
    """Render Pipeline Activity section from per-business created counts.

    Falls back to a 'no CRM data captured' note when the table has no rows for
    the week (historical backfill, or DB unreachable). Parser treats each
    ### heading as a river so the dashboard cards still render.
    """
    static_rosters = {
        "Agent Empire (Skool)": [
            "Community agent: Tammy (every 6h)",
            "Content producer: Debra (Mon 6am)",
            "Sponsor outreach: Wade (Mon 9am)",
        ],
        "CustomerAdvocate (Internal)": [
            "Technical builder: Clint (daily 10am)",
            "Web design: Sherry (daily 11am)",
        ],
    }
    biz_display = {
        "aiphoneguy": "AI Phone Guy (GoHighLevel)",
        "callingdigital": "Calling Digital (Attio)",
        "autointelligence": "Automotive Intelligence (HubSpot)",
    }
    revenue_rivers = ["aiphoneguy", "callingdigital", "autointelligence"]

    blocks = []
    for key in revenue_rivers:
        label = biz_display[key]
        created = by_business.get(key, 0)
        note = "Prospects created" if created else "No CRM activity captured this week"
        blocks.append(f"### {label}\n- {note}: {created}")
    for label, items in static_rosters.items():
        blocks.append(f"### {label}\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(blocks)


def _get_crm_pipeline_counts(week: int, year: int) -> dict:
    """Query crm_push_logs for prospects created during the given ISO week.

    Returns {"by_business": {biz: count, ...}, "total_created": N}. Falls back
    to empty dict if DB unavailable. This replaces the broken log-grep approach
    which looked for keywords ('ENROLLED', 'HOT LEAD') that never existed in
    the actual log files.
    """
    import os
    try:
        try:
            import psycopg2 as psycopg
        except ImportError:
            import psycopg  # type: ignore
    except ImportError:
        return {"by_business": {}, "total_created": 0}

    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"by_business": {}, "total_created": 0}

    try:
        monday = datetime.fromisocalendar(year, week, 1)
        next_monday = monday + timedelta(days=7)
        conn = psycopg.connect(url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT business_name, COUNT(*) FROM crm_push_logs "
                    "WHERE status = 'created' AND created_at >= %s AND created_at < %s "
                    "GROUP BY business_name",
                    (monday, next_monday),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        by_biz = {biz: count for biz, count in rows}
        return {"by_business": by_biz, "total_created": sum(by_biz.values())}
    except Exception as e:
        logger.warning(f"[Changelog] CRM pipeline query failed: {e}")
        return {"by_business": {}, "total_created": 0}


def _get_cost_section() -> str:
    """Build cost reporting section for changelog."""
    try:
        from core.cost_tracker import (
            get_agent_cost_summary, get_river_cost_summary,
            get_monthly_projection, get_most_expensive_run,
        )
        agent_costs = get_agent_cost_summary(days=7)
        river_costs = get_river_cost_summary(days=7)
        projection = get_monthly_projection()
        most_expensive = get_most_expensive_run(days=7)

        lines = []
        if river_costs:
            lines.append("### Cost by River (Last 7 Days)")
            for r in river_costs:
                lines.append(f"- {r['river']}: ${r['total_cost_usd']:.2f} ({r['total_runs']} runs)")
        if agent_costs:
            lines.append("\n### Top Agents by Cost")
            for a in agent_costs[:5]:
                lines.append(f"- {a['agent_name']}: ${a['total_cost_usd']:.2f} ({a['total_runs']} runs)")
        if most_expensive:
            lines.append(f"\n### Most Expensive Run")
            lines.append(f"- {most_expensive['agent_name']}: ${most_expensive['cost_usd']:.4f} on {most_expensive['run_date']}")
        lines.append(f"\n### Monthly Projection")
        lines.append(f"- Daily average: ${projection['daily_average']:.2f}")
        lines.append(f"- Projected monthly: ${projection['projected_monthly']:.2f}")

        return "\n".join(lines) if lines else "- No cost data available yet"
    except Exception:
        return "- Cost tracking not yet active"


def _get_git_activity(days: int = 7, week: int | None = None, year: int | None = None) -> dict:
    """Get git commit activity.

    If week/year provided, bounds to that ISO week (Mon–Sun). Otherwise uses last N days.
    """
    try:
        if week and year:
            since, until = _iso_week_bounds(week, year)
            args = ["git", "log", f"--since={since}", f"--until={until}", "--oneline", "--no-merges"]
        else:
            args = ["git", "log", f"--since={days} days ago", "--oneline", "--no-merges"]
        result = subprocess.run(args, capture_output=True, text=True, cwd=REPO_ROOT)
        commits = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]

        bugs_fixed = [c for c in commits if any(kw in c.lower() for kw in ["fix", "bug", "patch", "hotfix"])]
        features = [c for c in commits if any(kw in c.lower() for kw in ["add", "new", "feat", "build", "create"])]

        return {
            "total_commits": len(commits),
            "bugs_fixed": bugs_fixed,
            "features_added": features,
            "all_commits": commits,
        }
    except Exception as e:
        logger.error(f"Git activity failed: {e}")
        return {"total_commits": 0, "bugs_fixed": [], "features_added": [], "all_commits": []}


def generate_changelog(week: int | None = None, year: int | None = None) -> str:
    """Generate weekly changelog markdown.

    If week/year provided, generates for that historical ISO week (git-only for past weeks).
    Otherwise generates for the current week with live pipeline/cost metrics.
    """
    historical = week is not None and year is not None
    if not historical:
        week = _get_week_number()
        year = datetime.now().year
    now = datetime.now()

    # Pipeline counts sourced from crm_push_logs (authoritative) instead of log grep.
    # Works for both current and historical weeks as long as the DB has records.
    pipeline = _get_crm_pipeline_counts(week, year)
    by_biz = pipeline["by_business"]

    # Cost tracker can't be rewound — skip for historical weeks
    cost_section = "- Not captured (historical backfill)" if historical else _get_cost_section()

    git = _get_git_activity(week=week if historical else None, year=year if historical else None)
    talking_points = _generate_talking_points(week, year, git, by_biz)

    story_block = f"\n## The Story This Week\n{talking_points}\n\n---\n" if talking_points else ""

    changelog = f"""# CHANGELOG — Week {week}, {year}
## AVO — Weekly Build Report
Generated: {now.strftime("%Y-%m-%d %H:%M CST")}

---
{story_block}
## Pipeline Activity

{_render_pipeline_section(by_biz)}

---

## Revenue Impact
- Total prospects created: {pipeline['total_created']}
- Businesses active: {len(by_biz)}
- Active rivers: 5
- Active agents: 22

---

## Development Activity
- Commits this week: {git['total_commits']}
- Bugs fixed: {len(git['bugs_fixed'])}
- Features added: {len(git['features_added'])}

### Bugs Fixed
{chr(10).join(f'- {b}' for b in git['bugs_fixed']) if git['bugs_fixed'] else '- None this week'}

### Features Added
{chr(10).join(f'- {f}' for f in git['features_added']) if git['features_added'] else '- None this week'}

---

## AVO Cost Report
{cost_section}

---

## Next Week Priorities
- Monitor enrollment rates across all 5 rivers
- Review hot lead conversion rates
- Optimize sequence copy based on open/reply data
- Continue VERA behavioral scoring engine build
- Expand Agent Empire Skool community

---

*AVO — AI Business Operating System. $15,000 MRR across 5 rivers.*
*Michael shows up to close. Agents do everything else.*
*Built live for Agent Empire Skool community.*
*Named from Avoda — work is worship.*
"""

    # Write to repo root (local dev) + Postgres (prod persistence — Railway FS is ephemeral)
    filename = f"CHANGELOG_WEEK_{week}_{year}.md"
    filepath = os.path.join(REPO_ROOT, filename)
    try:
        with open(filepath, "w") as f:
            f.write(changelog)
    except OSError as e:
        logger.warning(f"[Changelog] Filesystem write failed ({e}) — DB is primary on Railway")

    try:
        from paperclip.changelog_view import write_to_db
        write_to_db(week, year, changelog)
    except Exception as e:
        logger.error(f"[Changelog] DB persist failed: {e}")

    logger.info(f"[Changelog] Generated {filename}")
    return filepath


def run_changelog(week: int | None = None, year: int | None = None):
    """Entry point for scheduler (no args) or admin backfill (week, year)."""
    try:
        filepath = generate_changelog(week=week, year=year)
        logger.info(f"[Changelog] Weekly changelog saved to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"[Changelog] Generation failed: {e}")
        raise


if __name__ == "__main__":
    run_changelog()
