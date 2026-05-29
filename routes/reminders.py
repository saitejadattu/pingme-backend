from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, HTTPException

from config.database import get_database
from middleware.auth_middleware import CurrentUser
from models.reminder import ManualReminderCreate, ReminderCreate, ReminderEdit, ReminderResponse
from services.calendar_service import create_calendar_event, delete_calendar_event, update_calendar_event
from services.gemini_service import parse_reminder
from services.scheduler_service import cancel_reminder_schedule, sync_reminder_schedule

router = APIRouter(prefix="/reminders", tags=["reminders"])
IST = ZoneInfo("Asia/Kolkata")


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def serialize_reminder(reminder: dict) -> ReminderResponse:
    return ReminderResponse(
        id=str(reminder["_id"]),
        user_id=str(reminder["user_id"]),
        title=reminder["title"],
        raw_input=reminder["raw_input"],
        remind_at=ensure_utc(reminder["remind_at"]),
        is_done=reminder.get("is_done", False),
        email_sent=reminder.get("email_sent", False),
        client_email=reminder.get("client_email"),
        client_name=reminder.get("client_name"),
        client_topic=reminder.get("client_topic"),
        client_message=reminder.get("client_message"),
        client_email_sent=reminder.get("client_email_sent", False),
        calendar_invite_sent=reminder.get("calendar_invite_sent", False),
        google_event_id=reminder.get("google_event_id"),
        sync_source=reminder.get("sync_source", "pingme"),
        reminder_attempt_count=reminder.get("reminder_attempt_count", 0),
        next_notification_at=ensure_utc(reminder["next_notification_at"]) if reminder.get("next_notification_at") else None,
        reminder_sequence_completed=reminder.get("reminder_sequence_completed", False),
        scheduler_provider=reminder.get("scheduler_provider"),
        scheduled_notification_jobs=reminder.get("scheduled_notification_jobs", []),
        scheduled_notification_times=[
            ensure_utc(scheduled_time) for scheduled_time in reminder.get("scheduled_notification_times", [])
        ],
        created_at=ensure_utc(reminder["created_at"]),
    )


async def _persist_reminder(user: dict, payload: dict) -> ReminderResponse:
    db = get_database()
    if not user.get("google_calendar_token"):
        raise HTTPException(
            status_code=403,
            detail="Connect Google Calendar from your profile before adding reminders.",
        )

    reminder_doc = {
        "user_id": user["_id"],
        "title": payload["title"],
        "raw_input": payload["raw_input"],
        "remind_at": payload["remind_at"],
        "is_done": False,
        "email_sent": False,
        "client_email": payload.get("client_email"),
        "client_name": payload.get("client_name"),
        "client_topic": payload.get("client_topic"),
        "client_message": payload.get("client_message"),
        "client_email_sent": False,
        "calendar_invite_sent": False,
        "google_event_id": None,
        "sync_source": "pingme",
        "reminder_attempt_count": 0,
        "next_notification_at": payload["remind_at"],
        "reminder_sequence_completed": False,
        "scheduler_provider": None,
        "scheduled_notification_jobs": [],
        "scheduled_notification_times": [],
        "created_at": datetime.now(timezone.utc),
    }
    try:
        reminder_doc["google_event_id"] = await create_calendar_event(user, reminder_doc)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="PingMe could not create the Google Calendar event. Reconnect Google Calendar and try again.",
        ) from exc

    if not reminder_doc["google_event_id"]:
        raise HTTPException(
            status_code=502,
            detail="Google Calendar did not return an event ID. Try reconnecting your calendar.",
        )

    result = await db.reminders.insert_one(reminder_doc)
    saved = await db.reminders.find_one({"_id": result.inserted_id})
    scheduler_metadata = await sync_reminder_schedule(saved)
    if scheduler_metadata:
        await db.reminders.update_one({"_id": result.inserted_id}, {"$set": scheduler_metadata})
        saved = await db.reminders.find_one({"_id": result.inserted_id})
    return serialize_reminder(saved)


@router.post("", response_model=ReminderResponse)
async def create_reminder(payload: ReminderCreate, current_user=CurrentUser):
    parsed = await parse_reminder(payload.raw_input, datetime.now(IST))
    remind_at = datetime.fromisoformat(parsed["remind_at"])
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=IST)
    return await _persist_reminder(
        current_user,
        {
            "title": parsed["title"],
            "raw_input": payload.raw_input,
            "remind_at": remind_at.astimezone(timezone.utc),
            "client_email": parsed.get("client_email"),
            "client_name": parsed.get("client_name"),
            "client_topic": parsed.get("client_topic"),
            "client_message": parsed.get("client_message"),
        },
    )


@router.post("/manual", response_model=ReminderResponse)
async def create_manual_reminder(payload: ManualReminderCreate, current_user=CurrentUser):
    remind_at = payload.remind_at
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=IST)
    return await _persist_reminder(
        current_user,
        {
            "title": payload.title,
            "raw_input": payload.raw_input,
            "remind_at": remind_at.astimezone(timezone.utc),
            "client_email": payload.client_email,
            "client_name": payload.client_name,
            "client_topic": payload.client_topic,
            "client_message": payload.client_message,
        },
    )


@router.get("", response_model=list[ReminderResponse])
async def list_reminders(current_user=CurrentUser):
    db = get_database()
    reminders = await db.reminders.find({"user_id": current_user["_id"]}).sort("remind_at", 1).to_list(500)
    return [serialize_reminder(reminder) for reminder in reminders]


@router.get("/date/{target_date}", response_model=list[ReminderResponse])
async def reminders_for_date(target_date: str, current_user=CurrentUser):
    db = get_database()
    day = date.fromisoformat(target_date)
    start = datetime.combine(day, time.min, tzinfo=IST).astimezone(timezone.utc)
    end = (datetime.combine(day, time.min, tzinfo=IST) + timedelta(days=1)).astimezone(timezone.utc)
    reminders = await db.reminders.find(
        {
            "user_id": current_user["_id"],
            "remind_at": {"$gte": start, "$lt": end},
        }
    ).sort("remind_at", 1).to_list(500)
    return [serialize_reminder(reminder) for reminder in reminders]


@router.patch("/{reminder_id}/done", response_model=ReminderResponse)
async def mark_done(reminder_id: str, current_user=CurrentUser):
    db = get_database()
    reminder = await db.reminders.find_one({"_id": ObjectId(reminder_id), "user_id": current_user["_id"]})
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    schedule_cleanup = await cancel_reminder_schedule(reminder)
    await db.reminders.update_one(
        {"_id": ObjectId(reminder_id), "user_id": current_user["_id"]},
        {
            "$set": {
                "is_done": True,
                "reminder_sequence_completed": True,
                "next_notification_at": None,
                **schedule_cleanup,
            }
        },
    )
    reminder = await db.reminders.find_one({"_id": ObjectId(reminder_id), "user_id": current_user["_id"]})
    return serialize_reminder(reminder)


@router.delete("/{reminder_id}")
async def delete_reminder(reminder_id: str, current_user=CurrentUser):
    db = get_database()
    reminder = await db.reminders.find_one({"_id": ObjectId(reminder_id), "user_id": current_user["_id"]})
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    await cancel_reminder_schedule(reminder)
    if reminder.get("google_event_id"):
        try:
            await delete_calendar_event(current_user, reminder["google_event_id"])
        except Exception:
            pass
    await db.reminders.delete_one({"_id": reminder["_id"]})
    return {"message": "Reminder deleted"}


@router.patch("/{reminder_id}", response_model=ReminderResponse)
async def edit_reminder(reminder_id: str, payload: ReminderEdit, current_user=CurrentUser):
    db = get_database()
    reminder = await db.reminders.find_one({"_id": ObjectId(reminder_id), "user_id": current_user["_id"]})
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")

    remind_at = payload.remind_at
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=IST)
    remind_at_utc = remind_at.astimezone(timezone.utc)
    raw_input = reminder.get("raw_input") or payload.title
    sync_source = reminder.get("sync_source", "pingme")
    if sync_source == "google":
        raw_input = payload.title

    updated_doc = {
        "title": payload.title,
        "raw_input": raw_input,
        "remind_at": remind_at_utc,
        "client_email": payload.client_email,
        "client_name": payload.client_name,
        "client_topic": payload.client_topic,
        "client_message": payload.client_message,
    }

    if reminder.get("google_event_id"):
        try:
            await update_calendar_event(
                current_user,
                reminder["google_event_id"],
                {
                    "title": payload.title,
                    "raw_input": raw_input,
                    "remind_at": remind_at_utc,
                },
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="PingMe could not update the Google Calendar event. Reconnect Google Calendar and try again.",
            ) from exc

    if remind_at_utc > datetime.now(timezone.utc):
        updated_doc["email_sent"] = False
        updated_doc["reminder_attempt_count"] = 0
        updated_doc["next_notification_at"] = remind_at_utc
        updated_doc["reminder_sequence_completed"] = False
        if payload.client_email:
            updated_doc["client_email_sent"] = False
        reminder_for_schedule = {**reminder, **updated_doc}
        updated_doc.update(await sync_reminder_schedule(reminder_for_schedule))

    await db.reminders.update_one(
        {"_id": reminder["_id"]},
        {"$set": updated_doc},
    )
    updated = await db.reminders.find_one({"_id": reminder["_id"]})
    return serialize_reminder(updated)
