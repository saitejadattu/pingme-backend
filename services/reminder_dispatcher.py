import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

from config.database import get_database
from services.email_service import send_client_followup_email, send_reminder_email

logger = logging.getLogger(__name__)

FOLLOW_UP_OFFSETS = [
    timedelta(minutes=0),
    timedelta(minutes=5),
    timedelta(minutes=25),
    timedelta(minutes=125),
    timedelta(days=2),
]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_next_notification_update(reminder: dict[str, Any]) -> dict[str, Any]:
    attempt_count = int(reminder.get("reminder_attempt_count", 0))
    next_attempt_count = attempt_count + 1
    update_fields: dict[str, Any] = {
        "email_sent": True,
        "reminder_attempt_count": next_attempt_count,
    }

    if next_attempt_count >= len(FOLLOW_UP_OFFSETS):
        update_fields["reminder_sequence_completed"] = True
        update_fields["next_notification_at"] = None
        return update_fields

    base_time = _ensure_utc(reminder["remind_at"])
    update_fields["next_notification_at"] = base_time + FOLLOW_UP_OFFSETS[next_attempt_count]
    return update_fields


def reminder_is_due(reminder: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if reminder.get("is_done") or reminder.get("reminder_sequence_completed"):
        return False

    next_notification_at = reminder.get("next_notification_at")
    if next_notification_at is not None:
        return _ensure_utc(next_notification_at) <= now

    if reminder.get("email_sent"):
        return False

    return _ensure_utc(reminder["remind_at"]) <= now


async def process_reminder(reminder: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    db = get_database()

    logger.info(
        "Sending reminder email for reminder %s to %s at %s",
        reminder["_id"],
        user.get("email"),
        reminder.get("next_notification_at") or reminder.get("remind_at"),
    )
    send_reminder_email(user, reminder)

    update_fields = _build_next_notification_update(reminder)

    if reminder.get("client_email") and not reminder.get("client_email_sent"):
        logger.info(
            "Sending client follow-up for reminder %s to %s",
            reminder["_id"],
            reminder.get("client_email"),
        )
        send_client_followup_email(reminder, user.get("name"))
        update_fields["client_email_sent"] = True

    await db.reminders.update_one({"_id": reminder["_id"]}, {"$set": update_fields})
    logger.info("Processed reminder %s", reminder["_id"])
    updated = await db.reminders.find_one({"_id": reminder["_id"]})
    return updated or reminder


async def process_reminder_by_id(reminder_id: str | ObjectId) -> dict[str, Any] | None:
    db = get_database()
    object_id = reminder_id if isinstance(reminder_id, ObjectId) else ObjectId(reminder_id)
    reminder = await db.reminders.find_one({"_id": object_id})
    if not reminder:
        logger.warning("Reminder %s not found for dispatch", reminder_id)
        return None

    if not reminder_is_due(reminder):
        logger.info("Reminder %s is not due for dispatch", reminder_id)
        return reminder

    user = await db.users.find_one({"_id": reminder["user_id"]})
    if not user:
        logger.warning("User %s not found for reminder %s", reminder.get("user_id"), reminder_id)
        return None

    return await process_reminder(reminder, user)


async def process_due_reminders(limit: int = 200) -> int:
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
    reminders = await cursor.to_list(length=limit)
    processed_count = 0
    for reminder in reminders:
        user = await db.users.find_one({"_id": reminder["user_id"]})
        if not user:
            continue
        try:
            await process_reminder(reminder, user)
            processed_count += 1
        except Exception as exc:
            logger.exception("Failed to process reminder %s: %s", reminder["_id"], exc)
    return processed_count
