"""Celery entry point. See the notification README for the commands that run it."""
from celery import Celery

from .... import models  # noqa: F401  Maps every table the foreign keys point at.
from ..config import settings
from .scheduler import BEAT_SCHEDULE

celery_app = Celery(
    "expense_notifications",
    broker=settings.redis_url,
    include=[
        "app.expense_manager.notification.worker.notification_tasks",
        "app.expense_manager.notification.worker.reminder_tasks",
    ],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    beat_schedule=BEAT_SCHEDULE,
    task_default_queue="expense_notifications",
    task_ignore_result=True,
    # The outbox is the durable record and already re-runs lost work, so tasks ack
    # on receipt. Acking late would add a second, competing redelivery mechanism.
    task_acks_late=False,
    task_soft_time_limit=45,
    task_time_limit=60,
    worker_prefetch_multiplier=4,
    broker_connection_retry_on_startup=True,
)
