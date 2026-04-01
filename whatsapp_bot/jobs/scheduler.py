"""
APScheduler setup — runs daily/weekly/monthly summaries and macro scans.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from whatsapp_bot.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    """Register all scheduled jobs and start the scheduler."""
    from whatsapp_bot.jobs.daily_summary import run_daily_summary
    from whatsapp_bot.jobs.weekly_summary import run_weekly_summary
    from whatsapp_bot.jobs.monthly_summary import run_monthly_summary
    from whatsapp_bot.jobs.macro_scan import run_macro_scan

    # Daily summary — every day at 20:00 UTC
    scheduler.add_job(
        run_daily_summary,
        trigger=CronTrigger(hour=20, minute=0),
        id="daily_summary",
        replace_existing=True,
    )

    # Weekly summary — every Monday at 08:00 UTC
    scheduler.add_job(
        run_weekly_summary,
        trigger=CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="weekly_summary",
        replace_existing=True,
    )

    # Monthly summary — 1st of every month at 08:00 UTC
    scheduler.add_job(
        run_monthly_summary,
        trigger=CronTrigger(day=1, hour=8, minute=0),
        id="monthly_summary",
        replace_existing=True,
    )

    # Macro scan — every N hours
    scheduler.add_job(
        run_macro_scan,
        trigger=IntervalTrigger(hours=settings.macro_scan_interval_hours),
        id="macro_scan",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with 4 jobs.")


def stop_scheduler():
    """Shutdown the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
