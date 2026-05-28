from datetime import datetime

from pydantic import BaseModel, Field


class GoogleCalendarEventEdit(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    start: datetime | str
    end: datetime | str
    is_all_day: bool = False
