import asyncio
import json
from typing import Any

from config.database import connect_to_mongo
from main import handler as asgi_handler
from services.reminder_dispatcher import process_reminder_by_id


def _ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return a usable event loop for the current Lambda invocation."""
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run_async(coro):
    loop = _ensure_event_loop()
    return loop.run_until_complete(coro)


async def _handle_scheduler_event(event: dict[str, Any]) -> dict[str, Any]:
    await connect_to_mongo()
    reminder_id = event.get("reminder_id")
    if not reminder_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "Missing reminder_id"}),
        }

    reminder = await process_reminder_by_id(reminder_id)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Reminder processed",
                "reminder_id": reminder_id,
                "found": bool(reminder),
            }
        ),
    }


def handler(event, context):
    if isinstance(event, dict) and event.get("action") == "dispatch_reminder":
        return _run_async(_handle_scheduler_event(event))
    _ensure_event_loop()
    return asgi_handler(event, context)
