"""Moves deliveries out of the outbox. Plain functions, so Celery, the cron command
and the in-process loop all run the same logic.

Delivery is at-least-once: a worker dying after FCM accepted a push but before
recording it causes a repeat. Clients ignore notification ids they already have.
"""
import logging
import random
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ....db import SessionLocal
from ....emerrgency.router import is_backend_blocked
from ..channels import DeliveryOutcome, push, websockets
from ..config import settings
from ..model import (
    DeliveryChannel,
    DeliveryStatus,
    ExpenseNotification,
    ExpenseNotificationDelivery,
    ExpenseNotificationDevice,
    utcnow,
)

logger = logging.getLogger(__name__)

CHANNELS = {
    DeliveryChannel.push.value: push.deliver,
    DeliveryChannel.websocket.value: websockets.deliver,
}


def claim_due(db: Session, limit: Optional[int] = None) -> list[int]:
    """Reserve due deliveries for this dispatcher and return their ids.

    There is no separate in-progress state: claiming pushes `next_attempt_at`
    forward, so anything that isn't finished comes due again by itself. SKIP
    LOCKED lets several dispatchers run side by side without taking the same rows.
    """
    now = utcnow()
    rows = db.query(
        ExpenseNotificationDelivery.id,
        ExpenseNotificationDelivery.attempts,
    ).filter(
        ExpenseNotificationDelivery.status == DeliveryStatus.pending.value,
        ExpenseNotificationDelivery.next_attempt_at <= now,
    ).order_by(
        ExpenseNotificationDelivery.next_attempt_at,
    ).limit(limit or settings.dispatch_batch_size).with_for_update(skip_locked=True).all()

    # Rows that reach this point with attempts spent were claimed and then lost.
    exhausted = [row.id for row in rows if row.attempts >= settings.max_attempts]
    claimed = [row.id for row in rows if row.attempts < settings.max_attempts]

    if exhausted:
        db.query(ExpenseNotificationDelivery).filter(
            ExpenseNotificationDelivery.id.in_(exhausted)
        ).update(
            {
                ExpenseNotificationDelivery.status: DeliveryStatus.dead.value,
                ExpenseNotificationDelivery.last_error: "Delivery was claimed but never finished.",
            },
            synchronize_session=False,
        )
    if claimed:
        db.query(ExpenseNotificationDelivery).filter(
            ExpenseNotificationDelivery.id.in_(claimed)
        ).update(
            {
                ExpenseNotificationDelivery.attempts: ExpenseNotificationDelivery.attempts + 1,
                ExpenseNotificationDelivery.next_attempt_at: (
                    now + timedelta(seconds=settings.claim_visibility_seconds)
                ),
            },
            synchronize_session=False,
        )
    db.commit()
    return claimed


def _retry_delay(attempts: int) -> timedelta:
    delay = min(
        settings.retry_base_seconds * 2 ** max(attempts - 1, 0),
        settings.retry_max_seconds,
    )
    # Jitter stops everything that failed together from retrying together.
    return timedelta(seconds=delay * random.uniform(1, 1.25))


def _apply_outcome(delivery: ExpenseNotificationDelivery, outcome: DeliveryOutcome) -> None:
    delivery.last_error = outcome.error
    delivery.pending_device_ids = outcome.retry_device_ids

    if outcome.status != DeliveryStatus.pending:
        delivery.status = outcome.status.value
        if outcome.status == DeliveryStatus.sent:
            delivery.sent_at = utcnow()
    elif delivery.attempts >= settings.max_attempts:
        delivery.status = DeliveryStatus.dead.value
    else:
        delivery.next_attempt_at = utcnow() + _retry_delay(delivery.attempts)


def deliver(delivery_id: int) -> None:
    """Attempt one claimed delivery and record what happened."""
    with SessionLocal() as db:
        delivery = db.get(ExpenseNotificationDelivery, delivery_id)
        if delivery is None or delivery.status != DeliveryStatus.pending.value:
            return

        notification = delivery.notification
        oldest_useful = utcnow() - timedelta(hours=settings.delivery_max_age_hours)
        if notification.created_at < oldest_useful:
            outcome = DeliveryOutcome(DeliveryStatus.skipped, "Too old to deliver.")
        else:
            try:
                outcome = CHANNELS[delivery.channel](db, delivery, notification)
            except Exception as exc:
                logger.exception("Delivery %s failed", delivery_id)
                outcome = DeliveryOutcome(
                    DeliveryStatus.pending,
                    str(exc)[:500],
                    delivery.pending_device_ids,
                )

        _apply_outcome(delivery, outcome)
        if delivery.status == DeliveryStatus.dead.value:
            logger.error(
                "Delivery %s gave up after %s attempts: %s",
                delivery_id,
                delivery.attempts,
                delivery.last_error,
            )
        db.commit()


def dispatch_due(enqueue: Callable[[int], None] = deliver) -> int:
    """Claim a batch and hand each delivery to `enqueue`. Returns how many were claimed."""
    if is_backend_blocked():
        return 0

    with SessionLocal() as db:
        delivery_ids = claim_due(db)
    for delivery_id in delivery_ids:
        enqueue(delivery_id)
    return len(delivery_ids)


def _delete_in_batches(db: Session, model, *criteria, batch_size: int = 1000) -> int:
    deleted = 0
    while True:
        ids = [row.id for row in db.query(model.id).filter(*criteria).limit(batch_size)]
        if not ids:
            return deleted
        deleted += db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        # Committing per batch keeps locks short while the app is writing.
        db.commit()


def purge_expired() -> dict[str, int]:
    """Enforce retention, keeping these tables from growing forever."""
    now = utcnow()
    with SessionLocal() as db:
        deliveries = _delete_in_batches(
            db,
            ExpenseNotificationDelivery,
            ExpenseNotificationDelivery.status != DeliveryStatus.pending.value,
            ExpenseNotificationDelivery.created_at
            < now - timedelta(days=settings.delivery_retention_days),
        )
        read = _delete_in_batches(
            db,
            ExpenseNotification,
            ExpenseNotification.read_at.is_not(None),
            ExpenseNotification.created_at < now - timedelta(days=settings.read_retention_days),
        )
        unread = _delete_in_batches(
            db,
            ExpenseNotification,
            ExpenseNotification.created_at < now - timedelta(days=settings.unread_retention_days),
        )
        devices = db.query(ExpenseNotificationDevice).filter(
            ExpenseNotificationDevice.is_active.is_(True),
            ExpenseNotificationDevice.last_seen_at
            < now - timedelta(days=settings.device_stale_days),
        ).update(
            {ExpenseNotificationDevice.is_active: False},
            synchronize_session=False,
        )
        db.commit()
    return {
        "deliveries": deliveries,
        "notifications": read + unread,
        "devices_deactivated": devices,
    }
