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
            # Try to parse timestamp from log line
            try:
                ts_str = line.split("|")[0].strip()
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
                if ts >= cutoff:
                    count += 1
            except (ValueError, IndexError):
                count += 1  # Count if we can't parse timestamp
    return count


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


def _get_git_activity(days: int = 7) -> dict:
    """Get git commit activity for the week."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--oneline", "--no-merges"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
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


def generate_changelog() -> str:
    """Generate weekly changelog markdown."""
    week_num = _get_week_number()
    year = datetime.now().year
    now = datetime.now()

    # Gather metrics from each river
    apg_enrolled = _count_log_events("ai_phone_guy_enrollments.log", "ENROLLED")
    cd_enrolled = _count_log_events("calling_digital_enrollments.log", "ENROLLED")
    ai_enrolled = _count_log_events("automotive_intelligence_enrollments.log", "ENROLLED")

    apg_hot = _count_log_events("ai_phone_guy_hot_leads.log", "HOT LEAD")
    cd_hot = _count_log_events("calling_digital_hot_leads.log", "HOT LEAD")
    ai_hot = _count_log_events("automotive_intelligence_hot_leads.log", "HOT LEAD")

    git = _get_git_activity()
    cost_section = _get_cost_section()

    changelog = f"""# CHANGELOG — Week {week_num}, {year}
## AVO — Weekly Build Report
Generated: {now.strftime("%Y-%m-%d %H:%M CST")}

---

## Pipeline Activity (Last 7 Days)

### AI Phone Guy (GoHighLevel)
- Contacts enrolled: {apg_enrolled}
- Hot leads triggered: {apg_hot}
- RevOps agent: Randy (every 4h)

### Calling Digital (Attio)
- Contacts enrolled: {cd_enrolled}
- Hot leads triggered: {cd_hot}
- RevOps agent: Brenda (every 2h)

### Automotive Intelligence (HubSpot)
- Contacts enrolled: {ai_enrolled}
- Hot leads triggered: {ai_hot}
- RevOps agent: Darrell (every 1h)

### Agent Empire (Skool)
- Community agent: Tammy (every 6h)
- Content producer: Debra (Mon 6am)
- Sponsor outreach: Wade (Mon 9am)

### CustomerAdvocate (Internal)
- Technical builder: Clint (daily 10am)
- Web design: Sherry (daily 11am)

---

## Revenue Impact
- Total contacts enrolled: {apg_enrolled + cd_enrolled + ai_enrolled}
- Total hot leads: {apg_hot + cd_hot + ai_hot}
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

    # Write to repo root
    filename = f"CHANGELOG_WEEK_{week_num}_{year}.md"
    filepath = os.path.join(REPO_ROOT, filename)
    with open(filepath, "w") as f:
        f.write(changelog)

    logger.info(f"[Changelog] Generated {filename}")
    return filepath


def run_changelog():
    """Entry point for scheduler — Friday 5pm CST."""
    try:
        filepath = generate_changelog()
        logger.info(f"[Changelog] Weekly changelog saved to {filepath}")
    except Exception as e:
        logger.error(f"[Changelog] Generation failed: {e}")


if __name__ == "__main__":
    run_changelog()
