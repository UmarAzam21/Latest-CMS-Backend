import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Celery workers import this before anything else has loaded the .env file.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")


def _int(name: str, default: int):
    return field(default_factory=lambda: int(os.getenv(name, default)))


def _str(name: str):
    return field(default_factory=lambda: os.getenv(name) or None)


@dataclass
class NotificationSettings:
    # Enables Celery and multi-process WebSocket fan-out. Without it the API
    # process dispatches the outbox itself, which only suits a single process.
    redis_url: Optional[str] = _str("REDIS_URL")

    # Service account for FCM: a file path, or the JSON itself for secret stores.
    # With neither set, push deliveries are recorded as skipped.
    firebase_credentials_path: Optional[str] = _str("FIREBASE_CREDENTIALS_PATH")
    firebase_credentials_json: Optional[str] = _str("FIREBASE_CREDENTIALS_JSON")

    # Web push click-through needs an absolute https URL.
    web_base_url: Optional[str] = _str("NOTIFICATION_WEB_BASE_URL")
    web_icon_url: Optional[str] = _str("NOTIFICATION_WEB_ICON_URL")

    dispatch_interval_seconds: int = _int("NOTIFICATION_DISPATCH_INTERVAL_SECONDS", 3)
    dispatch_batch_size: int = _int("NOTIFICATION_DISPATCH_BATCH_SIZE", 500)
    # A claimed delivery becomes claimable again after this long, which is how
    # work lost to a crashed worker or a broker outage gets picked back up.
    claim_visibility_seconds: int = _int("NOTIFICATION_CLAIM_VISIBILITY_SECONDS", 300)
    max_attempts: int = _int("NOTIFICATION_MAX_ATTEMPTS", 6)
    retry_base_seconds: int = _int("NOTIFICATION_RETRY_BASE_SECONDS", 30)
    retry_max_seconds: int = _int("NOTIFICATION_RETRY_MAX_SECONDS", 3600)
    # After an outage, drop deliveries too old to be useful instead of flooding devices.
    delivery_max_age_hours: int = _int("NOTIFICATION_DELIVERY_MAX_AGE_HOURS", 24)
    push_ttl_seconds: int = _int("NOTIFICATION_PUSH_TTL_SECONDS", 86400)

    max_devices_per_user: int = _int("NOTIFICATION_MAX_DEVICES_PER_USER", 10)
    ws_auth_timeout_seconds: int = _int("NOTIFICATION_WS_AUTH_TIMEOUT_SECONDS", 10)
    ws_max_connections_per_user: int = _int("NOTIFICATION_WS_MAX_CONNECTIONS_PER_USER", 10)

    # A card alert fires when its balance drops to this value or below.
    low_balance_threshold: int = _int("NOTIFICATION_LOW_BALANCE_THRESHOLD", 0)
    reminder_hour_utc: int = _int("NOTIFICATION_REMINDER_HOUR_UTC", 4)

    read_retention_days: int = _int("NOTIFICATION_READ_RETENTION_DAYS", 90)
    unread_retention_days: int = _int("NOTIFICATION_UNREAD_RETENTION_DAYS", 180)
    delivery_retention_days: int = _int("NOTIFICATION_DELIVERY_RETENTION_DAYS", 30)
    # Firebase treats tokens unused for 270 days as expired.
    device_stale_days: int = _int("NOTIFICATION_DEVICE_STALE_DAYS", 270)


settings = NotificationSettings()
