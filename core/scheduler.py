"""APScheduler configuration for AVO daily summary.

NOTE: River agent jobs (Randy/Brenda/Darrell/Tammy/Wade/Debra/Sterling/Clint/Sherry)
are registered directly in app.py via the AVO intelligence wrapper (_avo_wrap_run)
so they get memory + directives + handoffs + cost tracking.

This module now only registers the daily summary notification — the per-agent
duplicate registrations were removed (they were firing every workflow twice and
bypassing the AVO intelligence layer).
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from core.logger import log_info


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="America/Chicago")
    return scheduler


def register_all_jobs(scheduler: BackgroundScheduler):
    """Register the daily summary notification.

    All agent jobs are registered in app.py via _avo_sched_* / _run_* wrappers.
    Do NOT add agent jobs here — they will fire twice and bypass the AVO
    intelligence layer (memory, directives, handoffs, cost tracking).
    """
    from core.notifier import notify_daily_summary
    from rivers.ai_phone_guy.workflow import get_stats as apg_stats
    from rivers.calling_digital.workflow import get_stats as cd_stats
    from rivers.automotive_intelligence.workflow import get_stats as ai_stats
    from rivers.agent_empire.workflow import get_stats as ae_stats

    def daily_summary():
        stats = {
            "AI Phone Guy": apg_stats(),
            "Calling Digital": cd_stats(),
            "Automotive Intelligence": ai_stats(),
            "Agent Empire": ae_stats(),
        }
        notify_daily_summary(stats)

    scheduler.add_job(
        daily_summary, CronTrigger(hour=8, minute=0, timezone="America/Chicago"),
        id="daily_summary", name="Daily Summary to Michael", replace_existing=True
    )

    log_info("scheduler", "Daily summary job registered (river agents handled by AVO wrapper in app.py)")
