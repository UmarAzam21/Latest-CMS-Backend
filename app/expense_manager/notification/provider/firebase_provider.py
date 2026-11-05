import json
import time
from typing import Optional

import firebase_admin
from firebase_admin import credentials, exceptions, messaging

from .base_provider import PushMessage, PushProvider, PushResult, PushStatus, PushTarget

# A multicast request accepts at most this many tokens and FIDs combined.
FCM_BATCH_SIZE = 500
APP_NAME = "expense-manager"


class FirebasePushProvider(PushProvider):
    def __init__(
        self,
        credentials_path: Optional[str] = None,
        credentials_json: Optional[str] = None,
    ):
        certificate = json.loads(credentials_json) if credentials_json else credentials_path
        self._app = firebase_admin.initialize_app(
            credentials.Certificate(certificate),
            name=APP_NAME,
        )

    def send(self, targets: list[PushTarget], message: PushMessage) -> list[PushResult]:
        results = []
        for start in range(0, len(targets), FCM_BATCH_SIZE):
            batch = targets[start:start + FCM_BATCH_SIZE]
            tokens = [target.identifier for target in batch if not target.is_fid]
            fids = [target.identifier for target in batch if target.is_fid]
            response = messaging.send_each_for_multicast(
                _build_message(tokens, fids, message),
                app=self._app,
            )
            # Firebase answers for tokens first, then FIDs, each in the order sent.
            results.extend(
                _to_result(identifier, item)
                for identifier, item in zip(tokens + fids, response.responses)
            )
        return results


def _to_result(identifier: str, response: messaging.SendResponse) -> PushResult:
    if response.success:
        return PushResult(identifier, PushStatus.delivered)

    error = response.exception
    message = str(error)[:200]
    if isinstance(error, (messaging.UnregisteredError, messaging.SenderIdMismatchError)):
        return PushResult(identifier, PushStatus.invalid_token, message)
    if isinstance(error, exceptions.InvalidArgumentError):
        return PushResult(identifier, PushStatus.rejected, message)
    return PushResult(identifier, PushStatus.retryable, message)


def _build_message(
    tokens: list[str],
    fids: list[str],
    message: PushMessage,
) -> messaging.MulticastMessage:
    """Build one payload with the right delivery settings for Android, iOS and web."""
    return messaging.MulticastMessage(
        tokens=tokens or None,
        fids=fids or None,
        data=message.data,
        notification=messaging.Notification(title=message.title, body=message.body),
        android=messaging.AndroidConfig(
            priority="high" if message.high_priority else "normal",
            ttl=message.ttl_seconds,
            notification=messaging.AndroidNotification(
                channel_id=f"expense_{message.category}",
            ),
        ),
        apns=messaging.APNSConfig(
            headers={
                "apns-priority": "10" if message.high_priority else "5",
                "apns-expiration": str(int(time.time()) + message.ttl_seconds),
            },
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    sound="default",
                    badge=message.badge,
                    thread_id=message.category,
                ),
            ),
        ),
        webpush=messaging.WebpushConfig(
            headers={
                "TTL": str(message.ttl_seconds),
                "Urgency": "high" if message.high_priority else "normal",
            },
            notification=messaging.WebpushNotification(
                icon=message.icon,
                tag=message.data.get("notification_id"),
            ),
            fcm_options=(
                messaging.WebpushFCMOptions(link=message.link) if message.link else None
            ),
        ),
    )
