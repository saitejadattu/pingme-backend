from datetime import datetime, timedelta, timezone

from datetime import date

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query

from config.database import get_database
from middleware.auth_middleware import CurrentUser
from models.google_calendar import GoogleCalendarEventEdit
from routes.reminders import serialize_reminder
from services.calendar_service import (
    delete_calendar_event,
    get_google_auth_url,
    list_calendar_events,
    sync_google_calendar_range,
    update_google_calendar_event,
)

router = APIRouter(prefix="/google-calendar", tags=["google-calendar"])


def _parse_range(date_from: str, date_to: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.fromisoformat(date_from)
        end = datetime.fromisoformat(date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date range") from exc

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        end = start + timedelta(days=1)
    return start, end


@router.get("/connect")
async def connect_google_calendar(current_user=CurrentUser):
    return {"auth_url": get_google_auth_url(f"user:{current_user['_id']}")}


@router.get("/events")
async def get_google_calendar_events(
    date_from: str = Query(...),
    date_to: str = Query(...),
    current_user=CurrentUser,
):
    if not current_user.get("google_calendar_token"):
        return []

    start, end = _parse_range(date_from, date_to)

    return await list_calendar_events(current_user, start, end)


@router.post("/sync")
async def sync_google_calendar_events(
    date_from: str = Query(...),
    date_to: str = Query(...),
    current_user=CurrentUser,
):
    if not current_user.get("google_calendar_token"):
        return []

    start, end = _parse_range(date_from, date_to)

    reminders = await sync_google_calendar_range(current_user, start, end)
    return [serialize_reminder(reminder) for reminder in reminders]


@router.get("/range")
async def get_google_calendar_range(
    date_from: str = Query(...),
    date_to: str = Query(...),
    current_user=CurrentUser,
):
    if not current_user.get("google_calendar_token"):
        return {"reminders": [], "events": []}

    start, end = _parse_range(date_from, date_to)
    reminders = await sync_google_calendar_range(current_user, start, end)
    events = await list_calendar_events(current_user, start, end)
    return {
        "reminders": [serialize_reminder(reminder) for reminder in reminders],
        "events": events,
    }


@router.patch("/events/{event_id}")
async def edit_google_calendar_event(
    event_id: str,
    payload: GoogleCalendarEventEdit,
    current_user=CurrentUser,
):
    db = get_database()
    try:
      if payload.is_all_day:
          start_value = payload.start if isinstance(payload.start, str) else payload.start.date().isoformat()
          end_value = payload.end if isinstance(payload.end, str) else payload.end.date().isoformat()
      else:
          start_value = payload.start if isinstance(payload.start, datetime) else datetime.fromisoformat(str(payload.start))
          end_value = payload.end if isinstance(payload.end, datetime) else datetime.fromisoformat(str(payload.end))
          if start_value.tzinfo is None:
              start_value = start_value.replace(tzinfo=timezone.utc)
          if end_value.tzinfo is None:
              end_value = end_value.replace(tzinfo=timezone.utc)

      await update_google_calendar_event(
          current_user,
          event_id,
          payload.title,
          start_value,
          end_value,
          payload.is_all_day,
      )
    except Exception as exc:
      raise HTTPException(status_code=502, detail="Could not update Google Calendar event") from exc

    update_fields = {"title": payload.title}
    if payload.is_all_day:
        start_date = date.fromisoformat(str(start_value))
        update_fields["remind_at"] = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=5, minutes=30)
        update_fields["raw_input"] = payload.title
    else:
        update_fields["remind_at"] = start_value.astimezone(timezone.utc)
        update_fields["raw_input"] = payload.title

    await db.reminders.update_many(
        {"user_id": current_user["_id"], "google_event_id": event_id},
        {"$set": update_fields},
    )
    return {"message": "Google event updated"}


@router.delete("/events/{event_id}")
async def remove_google_calendar_event(event_id: str, current_user=CurrentUser):
    db = get_database()
    try:
        await delete_calendar_event(current_user, event_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not delete Google Calendar event") from exc

    await db.reminders.delete_many(
        {"user_id": current_user["_id"], "google_event_id": event_id}
    )
    return {"message": "Google event deleted"}
