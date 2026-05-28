from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ReminderCreate(BaseModel):
    raw_input: str = Field(min_length=2, max_length=500)


class ManualReminderCreate(BaseModel):
    raw_input: str = Field(min_length=2, max_length=500)
    title: str = Field(min_length=2, max_length=200)
    remind_at: datetime
    client_email: Optional[EmailStr] = None
    client_name: Optional[str] = Field(default=None, max_length=100)
    client_topic: Optional[str] = Field(default=None, max_length=200)
    client_message: Optional[str] = Field(default=None, max_length=2000)


class ReminderUpdate(BaseModel):
    is_done: bool = True


class ReminderEdit(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    remind_at: datetime
    client_email: Optional[EmailStr] = None
    client_name: Optional[str] = Field(default=None, max_length=100)
    client_topic: Optional[str] = Field(default=None, max_length=200)
    client_message: Optional[str] = Field(default=None, max_length=2000)


class ReminderResponse(BaseModel):
    id: str
    user_id: str
    title: str
    raw_input: str
    remind_at: datetime
    is_done: bool
    email_sent: bool
    client_email: Optional[EmailStr] = None
    client_name: Optional[str] = None
    client_topic: Optional[str] = None
    client_message: Optional[str] = None
    client_email_sent: bool
    calendar_invite_sent: bool = False
    google_event_id: Optional[str] = None
    sync_source: str = "pingme"
    reminder_attempt_count: int = 0
    next_notification_at: Optional[datetime] = None
    reminder_sequence_completed: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
