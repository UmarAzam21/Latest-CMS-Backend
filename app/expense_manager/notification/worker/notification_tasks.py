import logging

from ..config import settings
from . import outbox_dispatcher
from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="expense_notifications.deliver")
def deliver_task(delivery_id: int) -> None:
    outbox_dispatcher.deliver(delivery_id)


def _enqueue(delivery_id: int) -> None:
    # A task still queued when the claim runs out is dropped: by then the
    # delivery is claimable again, and running both would send it twice.
    deliver_task.apply_async((delivery_id,), expires=settings.claim_visibility_seconds)


@celery_app.task(name="expense_notifications.dispatch_outbox")
def dispatch_outbox_task() -> int:
    return outbox_dispatcher.dispatch_due(_enqueue)


@celery_app.task(name="expense_notifications.purge_expired")
def purge_expired_task() -> None:
    logger.info("Purged notification data: %s", outbox_dispatcher.purge_expired())
