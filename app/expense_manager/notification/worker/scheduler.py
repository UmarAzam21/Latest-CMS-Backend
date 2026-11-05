"""Decides what runs when, for each way the engine can be deployed.

- Redis available: Celery beat runs `BEAT_SCHEDULE`.
- No Redis, API only: the API process loops over the outbox itself.
- No Redis, cron available: `python -m app.expense_manager.notification.worker.scheduler
  dispatch`, likewise `reminders` and `purge`, each once per cron run.
"""
import argparse
import asyncio
import logging
from typing import Optional

from celery.schedules import crontab

from ..config import settings

logger = logging.getLogger(__name__)

BEAT_SCHEDULE = {
    "dispatch-outbox": {
        "task": "expense_notifications.dispatch_outbox",
        "schedule": float(settings.dispatch_interval_seconds),
        # A tick nobody picked up is pointless once the next one is due.
        "options": {"expires": settings.dispatch_interval_seconds},
    },
    "card-expiry-reminders": {
        "task": "expense_notifications.card_expiry_reminders",
        "schedule": crontab(hour=settings.reminder_hour_utc, minute=0),
    },
    "weekly-summaries": {
        "task": "expense_notifications.weekly_summaries",
        "schedule": crontab(hour=settings.reminder_hour_utc, minute=0, day_of_week="monday"),
    },
    "purge-expired": {
        "task": "expense_notifications.purge_expired",
        "schedule": crontab(hour=(settings.reminder_hour_utc + 18) % 24, minute=30),
    },
}


async def _run_inline_dispatcher() -> None:
    from .outbox_dispatcher import dispatch_due

    while True:
        try:
            await asyncio.to_thread(dispatch_due)
        except Exception:
            logger.exception("Notification dispatch failed")
        await asyncio.sleep(settings.dispatch_interval_seconds)


def start_inline_dispatcher() -> Optional[asyncio.Task]:
    """Run the outbox inside the API process when there is no Celery to do it."""
    if settings.redis_url:
        return None
    logger.warning(
        "REDIS_URL is not set: dispatching notifications from the API process. "
        "This suits one process only, and reminders need Celery beat or cron."
    )
    return asyncio.create_task(_run_inline_dispatcher())


def _run_once(command: str) -> None:
    from . import outbox_dispatcher, reminder_tasks

    if command == "dispatch":
        print(f"Delivered {outbox_dispatcher.dispatch_due()} notifications")
    elif command == "reminders":
        print(f"Queued {reminder_tasks.send_card_expiry_reminders()} card expiry reminders")
    elif command == "weekly-summaries":
        print(f"Queued {reminder_tasks.send_weekly_summaries()} weekly summaries")
    else:
        print(f"Purged: {outbox_dispatcher.purge_expired()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one notification job and exit.")
    parser.add_argument("command", choices=["dispatch", "reminders", "weekly-summaries", "purge"])
    _run_once(parser.parse_args().command)
