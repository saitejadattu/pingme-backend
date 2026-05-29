import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

SCHEDULER_PROVIDER = os.getenv("SCHEDULER_PROVIDER", "apscheduler").lower()
AWS_REGION = os.getenv("AWS_REGION", "")
AWS_SCHEDULER_GROUP = os.getenv("AWS_SCHEDULER_GROUP", "default")
AWS_SCHEDULER_ROLE_ARN = os.getenv("AWS_SCHEDULER_ROLE_ARN", "")
AWS_REMINDER_TARGET_ARN = os.getenv("AWS_REMINDER_TARGET_ARN", "")
FOLLOW_UP_OFFSETS = [
    timedelta(minutes=0),
    timedelta(minutes=5),
    timedelta(minutes=25),
    timedelta(minutes=125),
    timedelta(days=2),
]
_scheduler_client = None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_follow_up_schedule(remind_at: datetime) -> list[datetime]:
    base_time = _ensure_utc(remind_at)
    return [base_time + offset for offset in FOLLOW_UP_OFFSETS]


def _build_schedule_name(reminder_id: str, attempt_index: int, remind_at: datetime) -> str:
    base = _ensure_utc(remind_at).strftime("%Y%m%d%H%M%S")
    return f"pingme-{reminder_id}-{attempt_index}-{base}"


def _get_scheduler_client():
    global _scheduler_client
    if _scheduler_client is None:
        _scheduler_client = boto3.client("scheduler", region_name=AWS_REGION or None)
    return _scheduler_client


def _is_eventbridge_enabled() -> bool:
    return (
        SCHEDULER_PROVIDER == "eventbridge"
        and bool(AWS_SCHEDULER_ROLE_ARN)
        and bool(AWS_REMINDER_TARGET_ARN)
    )


def _create_eventbridge_schedule(
    reminder_id: str,
    attempt_index: int,
    run_at: datetime,
) -> str:
    client = _get_scheduler_client()
    schedule_name = _build_schedule_name(reminder_id, attempt_index, run_at)
    client.create_schedule(
        Name=schedule_name,
        GroupName=AWS_SCHEDULER_GROUP,
        ScheduleExpression=f"at({_ensure_utc(run_at).strftime('%Y-%m-%dT%H:%M:%S')})",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": AWS_REMINDER_TARGET_ARN,
            "RoleArn": AWS_SCHEDULER_ROLE_ARN,
            "Input": json.dumps(
                {
                    "action": "dispatch_reminder",
                    "reminder_id": reminder_id,
                    "attempt_index": attempt_index,
                }
            ),
        },
        ActionAfterCompletion="DELETE",
    )
    return schedule_name


def _delete_eventbridge_schedule(schedule_name: str) -> None:
    client = _get_scheduler_client()
    try:
        client.delete_schedule(Name=schedule_name, GroupName=AWS_SCHEDULER_GROUP)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code != "ResourceNotFoundException":
            raise


def build_scheduler_metadata(reminder: dict[str, Any]) -> dict[str, Any]:
    schedule_times = build_follow_up_schedule(reminder["remind_at"])
    # Local/dev mode does not create external scheduler jobs yet. We still store
    # the intended schedule so the reminder shape is ready for EventBridge.
    return {
        "scheduler_provider": SCHEDULER_PROVIDER,
        "scheduled_notification_jobs": [],
        "scheduled_notification_times": schedule_times,
    }


async def sync_reminder_schedule(reminder: dict[str, Any]) -> dict[str, Any]:
    reminder_id = str(reminder.get("_id") or "")
    if reminder_id and reminder.get("scheduled_notification_jobs"):
        await cancel_reminder_schedule(reminder)

    if _is_eventbridge_enabled() and reminder_id:
        scheduled_jobs: list[str] = []
        schedule_times = build_follow_up_schedule(reminder["remind_at"])
        for attempt_index, run_at in enumerate(schedule_times):
            if _ensure_utc(run_at) <= datetime.now(timezone.utc):
                continue
            schedule_name = _create_eventbridge_schedule(reminder_id, attempt_index, run_at)
            scheduled_jobs.append(schedule_name)

        metadata = {
            "scheduler_provider": "eventbridge",
            "scheduled_notification_jobs": scheduled_jobs,
            "scheduled_notification_times": schedule_times,
        }
        logger.info(
            "Prepared EventBridge schedules for reminder %s (%s job(s))",
            reminder.get("_id"),
            len(scheduled_jobs),
        )
        return metadata

    metadata = build_scheduler_metadata(reminder)
    logger.info(
        "Prepared scheduler metadata for reminder %s using provider %s",
        reminder.get("_id"),
        metadata["scheduler_provider"],
    )
    return metadata


async def cancel_reminder_schedule(reminder: dict[str, Any]) -> dict[str, Any]:
    if reminder.get("scheduler_provider") == "eventbridge":
        for schedule_name in reminder.get("scheduled_notification_jobs", []):
            _delete_eventbridge_schedule(schedule_name)
    logger.info(
        "Cancelling scheduler metadata for reminder %s using provider %s",
        reminder.get("_id"),
        reminder.get("scheduler_provider", SCHEDULER_PROVIDER),
    )
    return {
        "scheduled_notification_jobs": [],
        "scheduled_notification_times": [],
    }
