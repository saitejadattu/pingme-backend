import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.database import get_database
from services.email_service import send_client_followup_email, send_reminder_email

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "10"))
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"
FOLLOW_UP_OFFSETS = [
    timedelta(minutes=0),
    timedelta(minutes=5),
    timedelta(minutes=25),
    timedelta(minutes=125),
    timedelta(days=2),
]


async def check_due_reminders() -> None:
    db = get_database()
    now = datetime.now(timezone.utc)
    cursor = db.reminders.find(
        {
            "is_done": False,
            "reminder_sequence_completed": {"$ne": True},
            "$or": [
                {"next_notification_at": {"$lte": now}},
                {
                    "next_notification_at": {"$exists": False},
                    "email_sent": False,
                    "remind_at": {"$lte": now},
                },
            ],
        }
    )
    reminders = await cursor.to_list(length=200)
    for reminder in reminders:
        user = await db.users.find_one({"_id": reminder["user_id"]})
        if not user:
            continue
        try:
            logger.info(
                "Sending reminder email for reminder %s to %s at %s",
                reminder["_id"],
                user.get("email"),
                reminder.get("next_notification_at") or reminder.get("remind_at"),
            )
            send_reminder_email(user, reminder)
            attempt_count = int(reminder.get("reminder_attempt_count", 0))
            next_attempt_count = attempt_count + 1
            update_fields: dict[str, object] = {
                "email_sent": True,
                "reminder_attempt_count": next_attempt_count,
            }
            if reminder.get("client_email") and not reminder.get("client_email_sent"):
                logger.info(
                    "Sending client follow-up for reminder %s to %s",
                    reminder["_id"],
                    reminder.get("client_email"),
                )
                send_client_followup_email(reminder, user.get("name"))
                update_fields["client_email_sent"] = True
            if next_attempt_count >= len(FOLLOW_UP_OFFSETS):
                update_fields["reminder_sequence_completed"] = True
                update_fields["next_notification_at"] = None
            else:
                base_time = reminder.get("remind_at")
                if base_time.tzinfo is None:
                    base_time = base_time.replace(tzinfo=timezone.utc)
                update_fields["next_notification_at"] = base_time + FOLLOW_UP_OFFSETS[next_attempt_count]
            await db.reminders.update_one({"_id": reminder["_id"]}, {"$set": update_fields})
            logger.info("Processed reminder %s", reminder["_id"])
        except Exception as exc:
            logger.exception("Failed to process reminder %s: %s", reminder["_id"], exc)


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
