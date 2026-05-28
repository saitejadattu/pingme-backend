import os
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


class Database:
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None


database = Database()


async def connect_to_mongo() -> AsyncIOMotorDatabase:
    if database.db is not None:
        return database.db

    mongodb_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("DB_NAME", "pingme")
    if not mongodb_url:
        raise RuntimeError("MONGODB_URL is not configured")

    database.client = AsyncIOMotorClient(mongodb_url)
    database.db = database.client[db_name]

    await database.db.users.create_index("email", unique=True)
    await database.db.users.create_index("verification_token")
    await database.db.users.create_index("reset_token")
    await database.db.reminders.create_index([("user_id", 1), ("remind_at", 1)])
    await database.db.reminders.create_index(
        [("user_id", 1), ("google_event_id", 1)],
        unique=True,
        partialFilterExpression={"google_event_id": {"$type": "string"}},
    )
    await database.db.reminders.create_index(
        [("next_notification_at", 1), ("is_done", 1), ("reminder_sequence_completed", 1)]
    )
    await database.db.reminders.create_index(
        [("email_sent", 1), ("is_done", 1), ("remind_at", 1)]
    )
    return database.db


async def close_mongo_connection() -> None:
    if database.client is not None:
        database.client.close()
    database.client = None
    database.db = None


def get_database() -> AsyncIOMotorDatabase:
    if database.db is None:
        raise RuntimeError("Database not connected")
    return database.db
