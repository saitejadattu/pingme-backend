import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Gemini did not return JSON")
    return json.loads(match.group(0))


def _strip_email(text: str) -> str:
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "", text).strip(" ,.")


def _extract_client_name(raw_input: str) -> str | None:
    patterns = [
        r"\bwith\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
        r"\bto\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+(?:about|regarding|on)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_input)
        if match:
            return match.group(1).strip()
    return None


def _extract_topic(raw_input: str) -> str | None:
    lowered = raw_input.lower()
    patterns = [
        r"\b(?:about|regarding|on)\s+(.+?)(?:,\s*[\w.+-]+@[\w-]+\.[\w.-]+|$)",
        r"\bto discuss\s+(.+?)(?:,\s*[\w.+-]+@[\w-]+\.[\w.-]+|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" ,.")
            if topic:
                return topic
    return None


def _build_title(cleaned: str) -> str:
    title = _strip_email(cleaned)
    title = re.sub(
        r"\b(today|tomorrow|day after tomorrow|tonight|morning|evening|noon)\b",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b("
        + "|".join(sorted(MONTHS.keys(), key=len, reverse=True))
        + r")\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\b(" + "|".join(WEEKDAYS.keys()) + r")\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:about|regarding|on)\s+[^,]+$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\bto discuss\s+[^,]+$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    return title or "Untitled reminder"


def _build_client_followup_message(client_name: str | None, title: str, topic: str | None) -> str:
    subject_context = topic or title or "our conversation"
    return (
        f"I wanted to follow up on {subject_context}. "
        "Please let me know a convenient time to reconnect, or if there is anything you need from my side to move this forward.\n\n"
        "Looking forward to hearing from you."
    )


def _apply_explicit_date(raw_input: str, current_datetime: datetime, remind_at: datetime) -> datetime:
    lowered = raw_input.lower()
    month_pattern = (
        r"\b("
        + "|".join(sorted(MONTHS.keys(), key=len, reverse=True))
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b"
    )
    month_match = re.search(month_pattern, lowered)
    if month_match:
        month = MONTHS[month_match.group(1)]
        day = int(month_match.group(2))
        year = int(month_match.group(3)) if month_match.group(3) else current_datetime.year
        try:
            candidate = remind_at.replace(year=year, month=month, day=day)
        except ValueError:
            return remind_at
        if not month_match.group(3) and candidate < current_datetime:
            candidate = candidate.replace(year=year + 1)
        return candidate

    weekday_pattern = r"\b(" + "|".join(WEEKDAYS.keys()) + r")\b"
    weekday_match = re.search(weekday_pattern, lowered)
    if weekday_match:
        target_weekday = WEEKDAYS[weekday_match.group(1)]
        current_weekday = current_datetime.weekday()
        delta_days = (target_weekday - current_weekday) % 7
        if delta_days == 0:
            delta_days = 7
        return remind_at + timedelta(days=delta_days)

    return remind_at


def _fallback_parse(raw_input: str, current_datetime: datetime) -> dict[str, Any]:
    if current_datetime.tzinfo is None:
        current_datetime = current_datetime.replace(tzinfo=IST)
    else:
        current_datetime = current_datetime.astimezone(IST)
    cleaned = re.sub(
        r"\b(set reminder to|have to|need to|remind me to|don't forget to)\b",
        "",
        raw_input,
        flags=re.IGNORECASE,
    ).strip(" ,.")
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw_input)
    remind_at = current_datetime.replace(second=0, microsecond=0)
    lowered = raw_input.lower()
    if "day after tomorrow" in lowered:
        remind_at += timedelta(days=2)
    elif "tomorrow" in lowered:
        remind_at += timedelta(days=1)
    else:
        remind_at = _apply_explicit_date(raw_input, current_datetime, remind_at)
    if re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lowered):
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lowered)
        hour = int(match.group(1)) % 12
        minute = int(match.group(2) or 0)
        if match.group(3) == "pm":
            hour += 12
        remind_at = remind_at.replace(hour=hour, minute=minute)
    else:
        remind_at = remind_at.replace(hour=9, minute=0)

    title = _build_title(cleaned)
    client_name = _extract_client_name(raw_input)
    client_email = email_match.group(0) if email_match else None
    topic = _extract_topic(raw_input)
    client_message = None
    if client_email:
        client_message = _build_client_followup_message(client_name, title, topic)
    return {
        "title": title,
        "remind_at": remind_at.isoformat(),
        "client_email": client_email,
        "client_name": client_name,
        "client_topic": topic,
        "client_message": client_message,
    }


async def parse_reminder(raw_input: str, current_datetime: datetime) -> dict[str, Any]:
    if current_datetime.tzinfo is None:
        current_datetime = current_datetime.replace(tzinfo=IST)
    else:
        current_datetime = current_datetime.astimezone(IST)
    prompt = f"""
Today is {current_datetime.isoformat()} (timezone: Asia/Kolkata).

Parse this reminder text and return ONLY a JSON object:
{{
  "title": "string",
  "remind_at": "ISO8601 datetime",
  "client_email": "string or null",
  "client_name": "string or null",
  "client_topic": "string or null",
  "client_message": "string or null"
}}

Parsing rules:
- Remove filler words: set reminder to, have to, need to, remind me to, don't forget to
- today = current date
- tomorrow = current date + 1 day
- day after tomorrow = current date + 2 days
- weekday name = next upcoming weekday
- morning X or X am = that hour AM
- evening X or X pm = that hour PM
- tonight = 21:00
- noon = 12:00
- no time = default 09:00
- no date = today
- Extract email if present in text
- Extract client name if present
- If text includes context like "about Vercel deployment", use that context in the title cleanup and follow-up email
- Generate a short professional follow-up message if client email found

Return ONLY the JSON, no explanation, no markdown.

Reminder text: {raw_input}
""".strip()

    if not GEMINI_API_KEY:
        return _fallback_parse(raw_input, current_datetime)

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2},
                },
            )
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json(text)
            if not parsed.get("title") or not parsed.get("remind_at"):
                raise ValueError("Gemini response missing fields")
            return parsed
    except Exception as exc:
        logger.warning("Gemini parse failed, using fallback parser: %s", exc)
        fallback = _fallback_parse(raw_input, current_datetime)
        if not fallback.get("title"):
            raise HTTPException(status_code=422, detail="Unable to parse reminder") from exc
        return fallback
