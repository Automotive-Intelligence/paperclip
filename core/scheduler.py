"""APScheduler configuration for all Project Paperclip agents."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from core.logger import log_info


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="America/Chicago")
    return scheduler


def register_all_jobs(scheduler: BackgroundScheduler):
    from rivers.ai_phone_guy.workflow import randy_run
    from rivers.calling_digital.workflow import brenda_run
    from rivers.automotive_intelligence.workflow import darrell_run
    from rivers.agent_empire.workflow import tammy_run, wade_run

    # Randy — GHL / AI Phone Guy — every 4 hours
    scheduler.add_job(
        randy_run, IntervalTrigger(hours=4),
        id="randy_ghl", name="Randy — GHL Monitor", replace_existing=True
    )

    # Brenda — Attio / Calling Digital — every 2 hours
    scheduler.add_job(
        brenda_run, IntervalTrigger(hours=2),
        id="brenda_attio", name="Brenda — Attio Monitor", replace_existing=True
    )

    # Darrell — HubSpot / Automotive Intelligence — every 1 hour
    scheduler.add_job(
        darrell_run, IntervalTrigger(hours=1),
        id="darrell_hubspot", name="Darrell — HubSpot Monitor", replace_existing=True
    )

    # Tammy — Skool / Agent Empire — every 6 hours
    scheduler.add_job(
        tammy_run, IntervalTrigger(hours=6),
        id="tammy_skool", name="Tammy — Skool Monitor", replace_existing=True
    )

    # Wade — Gmail / Sponsor Outreach — Monday 9am CST
    scheduler.add_job(
        wade_run, CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="wade_gmail", name="Wade — Sponsor Outreach", replace_existing=True
    )

    # Daily 8am summary to Michael
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

    log_info("scheduler", "All jobs registered")
    for job in scheduler.get_jobs():
        log_info("scheduler", f"  {job.name} — next run: {job.next_run_time}")
