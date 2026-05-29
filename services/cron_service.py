import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.reminder_dispatcher import process_due_reminders

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "10"))
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"


async def check_due_reminders() -> None:
    processed_count = await process_due_reminders()
    if processed_count:
        logger.info("Processed %s due reminder(s)", processed_count)


def start_scheduler() -> None:
    if not ENABLE_SCHEDULER:
        logger.info("PingMe scheduler disabled by ENABLE_SCHEDULER=false")
        return
    if scheduler.running:
        return
    scheduler.add_job(
        check_due_reminders,
        "interval",
        seconds=SCHEDULER_INTERVAL_SECONDS,
        id="reminder-checker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "PingMe scheduler started with %s second interval",
        SCHEDULER_INTERVAL_SECONDS,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
