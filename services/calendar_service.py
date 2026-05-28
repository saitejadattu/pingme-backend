import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from bson import ObjectId
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.database import get_database

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
CALENDAR_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
AUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
IST = timezone(timedelta(hours=5, minutes=30))


async def _persist_refreshed_tokens(user_id: ObjectId, token_data: dict[str, Any], refresh_token: str | None) -> None:
    db = get_database()
    await db.users.update_one(
        {"_id": user_id},
        {
            "$set": {
                "google_calendar_token": token_data,
                "google_refresh_token": refresh_token,
            }
        },
    )


def get_google_auth_url(state: str, scopes: list[str] | None = None) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes or CALENDAR_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code_for_tokens(code: str, scopes: list[str] | None = None) -> dict[str, Any]:
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes=scopes or CALENDAR_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        "id_token": getattr(credentials, "id_token", None),
    }


async def fetch_google_user_info(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


def _build_credentials(user: dict[str, Any]) -> Credentials | None:
    token_data = user.get("google_calendar_token")
    if not token_data:
        return None
    original_token = token_data.get("token")
    original_expiry = token_data.get("expiry")
    original_refresh_token = user.get("google_refresh_token") or token_data.get("refresh_token")
    credentials = Credentials(
        token=original_token,
        refresh_token=original_refresh_token,
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id") or GOOGLE_CLIENT_ID,
        client_secret=token_data.get("client_secret") or GOOGLE_CLIENT_SECRET,
        scopes=token_data.get("scopes", CALENDAR_SCOPES),
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        refreshed_expiry = credentials.expiry.isoformat() if credentials.expiry else None
        refreshed_token = credentials.token
        refreshed_refresh_token = credentials.refresh_token or original_refresh_token
        if (
            refreshed_token != original_token
            or refreshed_expiry != original_expiry
            or refreshed_refresh_token != original_refresh_token
        ):
            refreshed_token_data = {
                **token_data,
                "token": refreshed_token,
                "refresh_token": refreshed_refresh_token,
                "expiry": refreshed_expiry,
            }
            loop = asyncio.get_running_loop()
            loop.create_task(
                _persist_refreshed_tokens(
                    ObjectId(user["_id"]),
                    refreshed_token_data,
                    refreshed_refresh_token,
                )
            )
    return credentials


def create_calendar_event(user: dict[str, Any], reminder: dict[str, Any]) -> str | None:
    credentials = _build_credentials(user)
    if credentials is None:
        return None
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    start = reminder["remind_at"]
    end = start + timedelta(hours=1)
    event = {
        "summary": reminder["title"],
        "description": reminder["raw_input"],
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Kolkata"},
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return created.get("id")


def update_calendar_event(user: dict[str, Any], event_id: str, reminder: dict[str, Any]) -> None:
    credentials = _build_credentials(user)
    if credentials is None:
        return
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    start = reminder["remind_at"]
    end = start + timedelta(hours=1)
    event = {
        "summary": reminder["title"],
        "description": reminder["raw_input"],
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Kolkata"},
    }
    service.events().patch(calendarId="primary", eventId=event_id, body=event).execute()


def update_google_calendar_event(
    user: dict[str, Any],
    event_id: str,
    title: str,
    start: datetime | str,
    end: datetime | str,
    is_all_day: bool,
) -> None:
    credentials = _build_credentials(user)
    if credentials is None:
        return
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    if is_all_day:
        event = {
            "summary": title,
            "start": {"date": str(start)},
            "end": {"date": str(end)},
        }
    else:
        start_dt = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        end_dt = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
        event = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        }
    service.events().patch(calendarId="primary", eventId=event_id, body=event).execute()


def _normalize_google_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("status") == "cancelled":
        return None
    start_data = event.get("start", {})
    end_data = event.get("end", {})
    start = start_data.get("dateTime") or start_data.get("date")
    end = end_data.get("dateTime") or end_data.get("date")
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "Untitled event",
        "description": event.get("description"),
        "start": start,
        "end": end,
        "is_all_day": "date" in start_data,
        "source": "google",
    }


def _parse_google_datetime(value: str, is_all_day: bool) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST if is_all_day else timezone.utc)
    return parsed.astimezone(timezone.utc)


def list_calendar_events(
    user: dict[str, Any],
    time_min: datetime,
    time_max: datetime,
) -> list[dict[str, Any]]:
    credentials = _build_credentials(user)
    if credentials is None:
        return []
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    response = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min.astimezone(timezone.utc).isoformat(),
            timeMax=time_max.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = []
    for event in response.get("items", []):
        normalized = _normalize_google_event(event)
        if normalized:
            events.append(normalized)
    return events


def get_calendar_event(user: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    credentials = _build_credentials(user)
    if credentials is None:
        return None
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except HttpError as exc:
        if getattr(exc, "status_code", None) == 404 or getattr(exc.resp, "status", None) == 404:
            return None
        raise
    return _normalize_google_event(event)


async def sync_google_calendar_range(
    user: dict[str, Any],
    time_min: datetime,
    time_max: datetime,
) -> list[dict[str, Any]]:
    db = get_database()
    google_events = list_calendar_events(user, time_min, time_max)
    google_by_id = {event["id"]: event for event in google_events}
    existing = await db.reminders.find(
        {
            "user_id": user["_id"],
            "google_event_id": {"$ne": None},
            "remind_at": {"$gte": time_min - timedelta(days=1), "$lt": time_max + timedelta(days=1)},
        }
    ).to_list(1000)
    existing_by_event_id: dict[str, list[dict[str, Any]]] = {}
    for reminder in existing:
        event_id = reminder.get("google_event_id")
        if event_id:
            existing_by_event_id.setdefault(event_id, []).append(reminder)

    now = datetime.now(timezone.utc)
    existing_ids = set(existing_by_event_id.keys())

    for event_id, duplicates in existing_by_event_id.items():
        if len(duplicates) <= 1:
            continue
        duplicates.sort(
            key=lambda reminder: (
                reminder.get("sync_source") != "pingme",
                reminder.get("created_at", now),
            )
        )
        primary = duplicates[0]
        for duplicate in duplicates[1:]:
            merged_fields = {
                "is_done": primary.get("is_done") or duplicate.get("is_done", False),
                "email_sent": primary.get("email_sent") or duplicate.get("email_sent", False),
                "client_email": primary.get("client_email") or duplicate.get("client_email"),
                "client_name": primary.get("client_name") or duplicate.get("client_name"),
                "client_message": primary.get("client_message") or duplicate.get("client_message"),
                "client_email_sent": primary.get("client_email_sent") or duplicate.get("client_email_sent", False),
            }
            await db.reminders.update_one({"_id": primary["_id"]}, {"$set": merged_fields})
            await db.reminders.delete_one({"_id": duplicate["_id"]})
        existing_by_event_id[event_id] = [await db.reminders.find_one({"_id": primary["_id"]})]

    for event_id, reminders_for_event in existing_by_event_id.items():
        reminder = reminders_for_event[0]
        if not event_id:
            continue
        event = google_by_id.get(event_id) or get_calendar_event(user, event_id)
        if event is None:
            await db.reminders.delete_one({"_id": reminder["_id"]})
            continue

        event_start = _parse_google_datetime(event["start"], event["is_all_day"])
        sync_source = reminder.get("sync_source", "pingme")
        update_fields: dict[str, Any] = {
            "title": event["title"],
            "remind_at": event_start,
            "sync_source": "google" if sync_source == "google" else sync_source,
        }
        if sync_source == "google":
            update_fields["raw_input"] = event.get("description") or event["title"]
        if reminder.get("remind_at") != event_start and event_start > now:
            update_fields["email_sent"] = False
            update_fields["reminder_attempt_count"] = 0
            update_fields["next_notification_at"] = event_start
            update_fields["reminder_sequence_completed"] = False
        if (
            reminder.get("title") != update_fields["title"]
            or (
                "raw_input" in update_fields
                and reminder.get("raw_input") != update_fields["raw_input"]
            )
            or reminder.get("remind_at") != update_fields["remind_at"]
            or reminder.get("email_sent") != update_fields.get("email_sent", reminder.get("email_sent"))
            or reminder.get("reminder_attempt_count") != update_fields.get("reminder_attempt_count", reminder.get("reminder_attempt_count"))
            or reminder.get("next_notification_at") != update_fields.get("next_notification_at", reminder.get("next_notification_at"))
            or reminder.get("reminder_sequence_completed") != update_fields.get("reminder_sequence_completed", reminder.get("reminder_sequence_completed"))
        ):
            await db.reminders.update_one({"_id": reminder["_id"]}, {"$set": update_fields})

    for event in google_events:
        existing_reminders = existing_by_event_id.get(event["id"], [])
        if existing_reminders:
            continue
        event_start = _parse_google_datetime(event["start"], event["is_all_day"])
        await db.reminders.insert_one(
            {
                "user_id": user["_id"],
                "title": event["title"],
                "raw_input": event.get("description") or event["title"],
                "remind_at": event_start,
                "is_done": False,
                "email_sent": False,
                "client_email": None,
                "client_name": None,
                "client_topic": None,
                "client_message": None,
                "client_email_sent": False,
                "calendar_invite_sent": False,
                "google_event_id": event["id"],
                "sync_source": "google",
                "reminder_attempt_count": 0 if event_start > now else 5,
                "next_notification_at": event_start if event_start > now else None,
                "reminder_sequence_completed": event_start <= now,
                "created_at": now,
            }
        )

    reminders = await db.reminders.find(
        {
            "user_id": user["_id"],
            "remind_at": {"$gte": time_min - timedelta(days=1), "$lt": time_max + timedelta(days=1)},
        }
    ).sort("remind_at", 1).to_list(1000)
    return reminders


def delete_calendar_event(user: dict[str, Any], event_id: str) -> None:
    credentials = _build_credentials(user)
    if credentials is None:
        return
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    service.events().delete(calendarId="primary", eventId=event_id).execute()
