"""Live updates for open apps and dashboards.

Sockets live in API processes while deliveries run in workers, so with Redis
configured every event goes through one pub/sub channel and each API process
forwards it to whichever sockets it holds. Events are a convenience on top of
the inbox: a client that was disconnected catches up by refetching it.
"""
import asyncio
import json
import logging
from functools import lru_cache
from typing import Optional

import redis
import redis.asyncio as aioredis
from fastapi import WebSocket
from sqlalchemy.orm import Session

from ..config import settings
from ..model import DeliveryStatus, ExpenseNotification, ExpenseNotificationDelivery
from ..schema import NotificationResponse
from . import DeliveryOutcome

logger = logging.getLogger(__name__)

REDIS_CHANNEL = "expense_notifications:events"
SEND_TIMEOUT_SECONDS = 5
MAX_RECONNECT_DELAY_SECONDS = 30


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listener: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if settings.redis_url:
            self._listener = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            await asyncio.gather(self._listener, return_exceptions=True)
            self._listener = None
        self._loop = None

    def connect(self, user_id: int, websocket: WebSocket) -> bool:
        sockets = self._connections.setdefault(user_id, set())
        if len(sockets) >= settings.ws_max_connections_per_user:
            return False
        sockets.add(websocket)
        return True

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._connections[user_id]

    async def _send(self, user_id: int, websocket: WebSocket, event: dict) -> None:
        try:
            await asyncio.wait_for(websocket.send_json(event), SEND_TIMEOUT_SECONDS)
        except Exception:
            self.disconnect(user_id, websocket)

    async def send_to_user(self, user_id: int, event: dict) -> None:
        sockets = list(self._connections.get(user_id, ()))
        await asyncio.gather(*(self._send(user_id, socket, event) for socket in sockets))

    def send_from_thread(self, user_id: int, event: dict) -> bool:
        if self._loop is None:
            return False
        asyncio.run_coroutine_threadsafe(self.send_to_user(user_id, event), self._loop)
        return True

    async def _listen(self) -> None:
        delay = 1
        while True:
            client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                health_check_interval=30,
            )
            try:
                async with client.pubsub() as pubsub:
                    await pubsub.subscribe(REDIS_CHANNEL)
                    delay = 1
                    while True:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=30,
                        )
                        if message is not None:
                            envelope = json.loads(message["data"])
                            await self.send_to_user(envelope["user_id"], envelope["event"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Lost the notification event stream; reconnecting.")
            finally:
                await client.aclose()
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)


ws_manager = ConnectionManager()


@lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(
        settings.redis_url,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def publish_event(user_id: int, event: dict) -> bool:
    """Send an event to the user's sockets from sync code, in any process.

    Returns False when nothing in this deployment can reach a socket.
    Raises if Redis is configured but unavailable.
    """
    if settings.redis_url:
        _redis_client().publish(
            REDIS_CHANNEL,
            json.dumps({"user_id": user_id, "event": event}),
        )
        return True
    return ws_manager.send_from_thread(user_id, event)


def deliver(
    db: Session,
    delivery: ExpenseNotificationDelivery,
    notification: ExpenseNotification,
) -> DeliveryOutcome:
    event = {
        "type": "notification.created",
        "data": NotificationResponse.model_validate(notification).model_dump(mode="json"),
    }
    if publish_event(notification.user_id, event):
        return DeliveryOutcome(DeliveryStatus.sent)
    return DeliveryOutcome(DeliveryStatus.skipped, "No WebSocket listener is available.")
