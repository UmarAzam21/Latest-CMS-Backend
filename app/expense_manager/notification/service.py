import hashlib
import logging
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models import ExpenseManagerBusiness
from .channels.websockets import publish_event
from .config import settings
from .events import (
    CATEGORY_POLICIES,
    NOTIFICATION_SPECS,
    NotificationCategory,
    NotificationType,
)
from .model import (
    DeliveryChannel,
    DevicePlatform,
    DeviceTokenType,
    ExpenseNotification,
    ExpenseNotificationDelivery,
    ExpenseNotificationDevice,
    ExpenseNotificationPreference,
    utcnow,
)

logger = logging.getLogger(__name__)


def _push_enabled(db: Session, user_id: int, category: NotificationCategory) -> bool:
    policy = CATEGORY_POLICIES[category]
    if policy.locked:
        return True
    preference = db.query(ExpenseNotificationPreference).filter(
        ExpenseNotificationPreference.user_id == user_id,
        ExpenseNotificationPreference.category == category.value,
    ).first()
    return preference.push_enabled if preference else policy.push_default


def _format_values(db: Session, business_id: Optional[int], context: dict[str, Any]) -> dict[str, Any]:
    if not any(isinstance(value, Decimal) for value in context.values()):
        return context

    business = db.get(ExpenseManagerBusiness, business_id) if business_id else None
    prefix = f"{business.currency} " if business else ""
    return {
        key: f"{prefix}{value:,.2f}" if isinstance(value, Decimal) else value
        for key, value in context.items()
    }


def _create(
    db: Session,
    user_id: int,
    notification_type: NotificationType,
    business_id: Optional[int],
    context: dict[str, Any],
    dedupe_key: Optional[str],
) -> Optional[ExpenseNotification]:
    if dedupe_key and db.query(ExpenseNotification.id).filter(
        ExpenseNotification.user_id == user_id,
        ExpenseNotification.dedupe_key == dedupe_key,
    ).first():
        return None

    spec = NOTIFICATION_SPECS[notification_type]
    values = _format_values(db, business_id, context)
    notification = ExpenseNotification(
        user_id=user_id,
        business_id=business_id,
        type=notification_type.value,
        category=spec.category.value,
        priority=spec.priority.value,
        title=spec.title.format(**values),
        body=spec.body.format(**values),
        data={key: str(value) for key, value in context.items()},
        action_url=spec.action_url.format(**values) if spec.action_url else None,
        dedupe_key=dedupe_key,
    )
    channels = [DeliveryChannel.websocket]
    if _push_enabled(db, user_id, spec.category):
        channels.append(DeliveryChannel.push)
    notification.deliveries = [
        ExpenseNotificationDelivery(channel=channel.value) for channel in channels
    ]
    db.add(notification)
    return notification


def publish(
    db: Session,
    *,
    user_id: int,
    notification_type: NotificationType,
    business_id: Optional[int] = None,
    context: Optional[dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
) -> Optional[ExpenseNotification]:
    """Queue a notification as part of the caller's transaction. Doesn't commit.

    It is saved only if the caller's work commits, and workers deliver it from
    the outbox afterwards, so no network call happens here. A problem creating
    the notification is logged rather than raised: it must never fail the
    money movement it describes. Returns None if nothing was queued, including
    when `dedupe_key` was already used for this user.
    """
    # A failure in the caller's own pending work must reach the caller as usual.
    db.flush()
    try:
        with db.begin_nested():
            return _create(
                db,
                user_id,
                notification_type,
                business_id,
                context or {},
                dedupe_key,
            )
    except Exception as exc:
        if not (dedupe_key and isinstance(exc, IntegrityError)):
            logger.exception("Could not queue %s for user %s", notification_type, user_id)
        return None


def notify_clients(user_id: int, event_type: str, data: dict[str, Any]) -> None:
    """Tell a user's other open clients about a change. Best effort, call after commit."""
    try:
        publish_event(user_id, {"type": event_type, "data": data})
    except Exception:
        logger.exception("Could not publish %s for user %s", event_type, user_id)


def register_device(
    db: Session,
    user_id: int,
    token: str,
    platform: DevicePlatform,
    token_type: DeviceTokenType = DeviceTokenType.fcm_token,
) -> ExpenseNotificationDevice:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    device = db.query(ExpenseNotificationDevice).filter(
        ExpenseNotificationDevice.token_hash == token_hash
    ).first()
    if device is None:
        device = ExpenseNotificationDevice(token=token, token_hash=token_hash)
        db.add(device)

    # A token follows whoever signed in last, so a shared or resold phone
    # stops receiving the previous user's notifications.
    device.user_id = user_id
    device.platform = platform.value
    device.token_type = token_type.value
    device.is_active = True
    device.last_seen_at = utcnow()
    try:
        db.flush()
    except IntegrityError:
        # Two registrations of a new token raced: the other one created the row.
        db.rollback()
        return register_device(db, user_id, token, platform, token_type)

    surplus = db.query(ExpenseNotificationDevice).filter(
        ExpenseNotificationDevice.user_id == user_id,
        ExpenseNotificationDevice.is_active.is_(True),
    ).order_by(ExpenseNotificationDevice.last_seen_at.desc()).offset(
        settings.max_devices_per_user
    ).all()
    for stale_device in surplus:
        stale_device.is_active = False

    db.commit()
    db.refresh(device)
    return device


def unregister_device(db: Session, user_id: int, device_id: int) -> None:
    device = db.query(ExpenseNotificationDevice).filter(
        ExpenseNotificationDevice.id == device_id,
        ExpenseNotificationDevice.user_id == user_id,
    ).first()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")
    device.is_active = False
    db.commit()


def list_preferences(db: Session, user_id: int) -> list[dict[str, Any]]:
    stored = {
        preference.category: preference.push_enabled
        for preference in db.query(ExpenseNotificationPreference).filter(
            ExpenseNotificationPreference.user_id == user_id
        )
    }
    return [
        {
            "category": category,
            "label": policy.label,
            "push_enabled": policy.locked or stored.get(category.value, policy.push_default),
            "locked": policy.locked,
        }
        for category, policy in CATEGORY_POLICIES.items()
    ]


def update_preference(
    db: Session,
    user_id: int,
    category: NotificationCategory,
    push_enabled: bool,
) -> list[dict[str, Any]]:
    if CATEGORY_POLICIES[category].locked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This notification category can't be turned off.",
        )

    preference = db.query(ExpenseNotificationPreference).filter(
        ExpenseNotificationPreference.user_id == user_id,
        ExpenseNotificationPreference.category == category.value,
    ).first()
    if preference is None:
        preference = ExpenseNotificationPreference(user_id=user_id, category=category.value)
        db.add(preference)
    preference.push_enabled = push_enabled
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request saved the same choice first; apply ours over it.
        db.rollback()
        return update_preference(db, user_id, category, push_enabled)
    return list_preferences(db, user_id)
