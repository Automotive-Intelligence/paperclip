"""Weekly changelog generator for Agent Empire build-in-public content.

Run: python scripts/changelog_gen.py
Output: CHANGELOG_WEEK_[N]_2026.md
Post to Agent Empire Skool every Saturday.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.logger import log_info


def generate_changelog():
    now = datetime.now()
    week_num = now.isocalendar()[1]
    year = now.year

    # Gather stats from each river
    stats = _gather_stats()

    changelog = f"""# CHANGELOG — Week {week_num}, {year}
**Generated:** {now.strftime('%Y-%m-%d %H:%M CST')}

## Empire Status
- Rivers Active: {stats['rivers_active']}
- Agents Online: {stats['agents_online']}

## Contacts Enrolled This Week
- AI Phone Guy (Randy): {stats.get('apg_enrolled', 0)}
- Calling Digital (Brenda): {stats.get('cd_enrolled', 0)}
- Automotive Intelligence (Darrell): {stats.get('ai_enrolled', 0)}
- Agent Empire (Tammy): {stats.get('ae_welcomed', 0)} new members

## Hot Leads
- Total hot leads flagged: {stats.get('hot_leads', 0)}

## Messages Sent
- SMS: {stats.get('sms_sent', 0)}
- Email: {stats.get('email_sent', 0)}
- Sponsor pitches: {stats.get('sponsor_pitches', 0)}

## Revenue Impact
- Pipeline value: ${stats.get('pipeline_value', 0):,}
- Deals created: {stats.get('deals_created', 0)}

## Bugs Fixed
{stats.get('bugs_fixed', '- None this week')}

## Next Week
{stats.get('next_week', '- Continue enrollment across all rivers')}

---
*Built in public. Posted to Agent Empire Skool.*
*Project Paperclip — $15K MRR target.*
"""

    filename = f"CHANGELOG_WEEK_{week_num}_{year}.md"
    filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), filename)
    Path(filepath).write_text(changelog)
    print(f"Changelog written: {filepath}")
    return filepath


def _gather_stats() -> dict:
    """Gather stats from all rivers. Falls back to zeros if not running."""
    stats = {
        "rivers_active": 5,
        "agents_online": 8,
        "apg_enrolled": 0,
        "cd_enrolled": 0,
        "ai_enrolled": 0,
        "ae_welcomed": 0,
        "hot_leads": 0,
        "sms_sent": 0,
        "email_sent": 0,
        "sponsor_pitches": 0,
        "pipeline_value": 0,
        "deals_created": 0,
        "bugs_fixed": "- None this week",
        "next_week": "- Continue enrollment across all rivers\n- Monitor hot lead conversion\n- Review sequence open rates",
    }

    try:
        from rivers.ai_phone_guy.workflow import get_stats
        apg = get_stats()
        stats["apg_enrolled"] = apg.get("enrolled", 0)
        stats["sms_sent"] += apg.get("messages_sent", 0)
    except Exception:
        pass

    try:
        from rivers.calling_digital.workflow import get_stats
        cd = get_stats()
        stats["cd_enrolled"] = cd.get("enrolled", 0)
        stats["email_sent"] += cd.get("messages_sent", 0)
    except Exception:
        pass

    try:
        from rivers.automotive_intelligence.workflow import get_stats
        ai = get_stats()
        stats["ai_enrolled"] = ai.get("enrolled", 0)
        stats["deals_created"] = ai.get("deals_created", 0)
        stats["email_sent"] += ai.get("messages_sent", 0)
    except Exception:
        pass

    try:
        from rivers.agent_empire.workflow import get_stats
        ae = get_stats()
        stats["ae_welcomed"] = ae.get("members_welcomed", 0)
        stats["sponsor_pitches"] = ae.get("sponsors_pitched", 0)
    except Exception:
        pass

    stats["hot_leads"] = stats.get("hot_leads", 0)
    return stats


if __name__ == "__main__":
    generate_changelog()
