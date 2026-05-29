import asyncio
import json
from typing import Any

from config.database import connect_to_mongo
from main import handler as asgi_handler
from services.reminder_dispatcher import process_reminder_by_id


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
        return asyncio.run(_handle_scheduler_event(event))
    return asgi_handler(event, context)
