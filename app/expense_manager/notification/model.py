from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ...db import Base

BigIntPK = BigInteger().with_variant(Integer, "sqlite")

# The database defaults to latin1, which would store Urdu or emoji as "?".
TABLE_OPTIONS = {"mysql_charset": "utf8mb4"}


def utcnow() -> datetime:
    # Columns are timezone-naive; store and compare everything as naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DeliveryChannel(str, Enum):
    push = "push"
    websocket = "websocket"


class DeliveryStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    skipped = "skipped"
    dead = "dead"


class DevicePlatform(str, Enum):
    android = "android"
    ios = "ios"
    web = "web"


class DeviceTokenType(str, Enum):
    # From getToken(), which Firebase has deprecated in favour of installation ids.
    fcm_token = "fcm_token"
    fid = "fid"


class ExpenseNotification(Base):
    """The inbox: what every client (mobile and web) reads, and the source of truth."""
    __tablename__ = "expense_notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # NULL means account-wide, such as a security alert: shown under every business.
    business_id = Column(
        Integer,
        ForeignKey("expense_manager_businesses.id", ondelete="CASCADE"),
        nullable=True,
    )
    type = Column(String(64), nullable=False)
    category = Column(String(32), nullable=False)
    priority = Column(String(16), nullable=False, default="normal")
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    data = Column(JSON, nullable=True)
    action_url = Column(String(512), nullable=True)
    dedupe_key = Column(String(191), nullable=True)
    read_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), nullable=False, default=utcnow)

    deliveries = relationship(
        "ExpenseNotificationDelivery",
        back_populates="notification",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_expense_notification_dedupe"),
        Index("ix_expense_notifications_feed", "user_id", "business_id", "created_at"),
        Index("ix_expense_notifications_unread", "user_id", "read_at"),
        Index("ix_expense_notifications_created_at", "created_at"),
        TABLE_OPTIONS,
    )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class ExpenseNotificationDelivery(Base):
    """Transactional outbox: one row per notification and channel, written with it."""
    __tablename__ = "expense_notification_deliveries"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    notification_id = Column(
        String(36),
        ForeignKey("expense_notifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default=DeliveryStatus.pending.value)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=False), nullable=False, default=utcnow)
    # Set after a partial push failure, so a retry skips devices already reached.
    pending_device_ids = Column(JSON, nullable=True)
    last_error = Column(String(500), nullable=True)
    sent_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), nullable=False, default=utcnow)

    notification = relationship("ExpenseNotification", back_populates="deliveries")

    __table_args__ = (
        UniqueConstraint("notification_id", "channel", name="uq_expense_notification_delivery"),
        Index("ix_expense_notification_deliveries_due", "status", "next_attempt_at"),
        TABLE_OPTIONS,
    )


class ExpenseNotificationDevice(Base):
    __tablename__ = "expense_notification_devices"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(16), nullable=False)
    token = Column(Text, nullable=False)
    token_type = Column(
        String(16),
        nullable=False,
        default=DeviceTokenType.fcm_token.value,
        server_default=DeviceTokenType.fcm_token.value,
    )
    # FCM tokens have no length guarantee, so uniqueness is enforced on a hash.
    token_hash = Column(String(64), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_seen_at = Column(DateTime(timezone=False), nullable=False, default=utcnow)
    created_at = Column(DateTime(timezone=False), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_expense_notification_devices_user", "user_id", "is_active"),
        TABLE_OPTIONS,
    )


class ExpenseNotificationPreference(Base):
    """Only choices the user has made are stored; otherwise the catalog default applies."""
    __tablename__ = "expense_notification_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(32), nullable=False)
    push_enabled = Column(Boolean, nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "category", name="uq_expense_notification_preference"),
        TABLE_OPTIONS,
    )
