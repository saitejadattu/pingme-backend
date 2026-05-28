from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from middleware.auth_middleware import CurrentUser
from routes.reminders import serialize_reminder
from services.calendar_service import get_google_auth_url, list_calendar_events, sync_google_calendar_range

router = APIRouter(prefix="/google-calendar", tags=["google-calendar"])


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

    return list_calendar_events(current_user, start, end)


@router.post("/sync")
async def sync_google_calendar_events(
    date_from: str = Query(...),
    date_to: str = Query(...),
    current_user=CurrentUser,
):
    if not current_user.get("google_calendar_token"):
        return []

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

    reminders = await sync_google_calendar_range(current_user, start, end)
    return [serialize_reminder(reminder) for reminder in reminders]
