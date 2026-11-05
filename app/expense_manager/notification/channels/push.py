import logging
from functools import lru_cache
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..model import (
    DeliveryStatus,
    DeviceTokenType,
    ExpenseNotification,
    ExpenseNotificationDelivery,
    ExpenseNotificationDevice,
)
from ..provider.base_provider import PushMessage, PushProvider, PushStatus, PushTarget
from . import DeliveryOutcome, in_app

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_push_provider() -> Optional[PushProvider]:
    if not (settings.firebase_credentials_path or settings.firebase_credentials_json):
        logger.warning("Firebase is not configured; push notifications are disabled.")
        return None
    # Imported here so processes that never send push don't load the Firebase SDK.
    from ..provider.firebase_provider import FirebasePushProvider

    return FirebasePushProvider(
        credentials_path=settings.firebase_credentials_path,
        credentials_json=settings.firebase_credentials_json,
    )


def _web_link(notification: ExpenseNotification) -> Optional[str]:
    base_url = settings.web_base_url
    if not base_url or not base_url.startswith("https://"):
        return None
    return f"{base_url.rstrip('/')}{notification.action_url or ''}"


def _build_message(db: Session, notification: ExpenseNotification) -> PushMessage:
    data = {
        "notification_id": notification.id,
        "type": notification.type,
        "category": notification.category,
    }
    if notification.action_url:
        data["action_url"] = notification.action_url
    if notification.business_id is not None:
        data["business_id"] = str(notification.business_id)

    return PushMessage(
        title=notification.title,
        body=notification.body,
        data=data,
        category=notification.category,
        high_priority=notification.priority == "high",
        ttl_seconds=settings.push_ttl_seconds,
        badge=in_app.total_unread_count(db, notification.user_id),
        link=_web_link(notification),
        icon=settings.web_icon_url,
    )


def deliver(
    db: Session,
    delivery: ExpenseNotificationDelivery,
    notification: ExpenseNotification,
) -> DeliveryOutcome:
    provider = get_push_provider()
    if provider is None:
        return DeliveryOutcome(DeliveryStatus.skipped, "Push provider is not configured.")

    query = db.query(ExpenseNotificationDevice).filter(
        ExpenseNotificationDevice.user_id == notification.user_id,
        ExpenseNotificationDevice.is_active.is_(True),
    )
    if delivery.pending_device_ids:
        query = query.filter(ExpenseNotificationDevice.id.in_(delivery.pending_device_ids))
    devices = {device.token: device for device in query.all()}
    if not devices:
        return DeliveryOutcome(DeliveryStatus.skipped, "No active devices.")

    targets = [
        PushTarget(token, is_fid=device.token_type == DeviceTokenType.fid.value)
        for token, device in devices.items()
    ]
    results = provider.send(targets, _build_message(db, notification))

    delivered = 0
    retry_device_ids = []
    errors = []
    for result in results:
        device = devices[result.identifier]
        if result.status == PushStatus.delivered:
            delivered += 1
        elif result.status == PushStatus.invalid_token:
            device.is_active = False
        elif result.status == PushStatus.retryable:
            retry_device_ids.append(device.id)
            errors.append(result.error or "Temporary push failure.")
        else:
            errors.append(result.error or "Push rejected.")

    error = "; ".join(errors)[:500] or None
    if retry_device_ids:
        return DeliveryOutcome(DeliveryStatus.pending, error, retry_device_ids)
    if delivered:
        return DeliveryOutcome(DeliveryStatus.sent, error)
    if errors:
        return DeliveryOutcome(DeliveryStatus.dead, error)
    return DeliveryOutcome(DeliveryStatus.skipped, "No reachable devices.")
