from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PushStatus(str, Enum):
    delivered = "delivered"
    # The token will never work again: stop sending to this device.
    invalid_token = "invalid_token"
    # Temporary failure, worth trying this device again.
    retryable = "retryable"
    # Refused for this message, so retrying can't help.
    rejected = "rejected"


@dataclass(frozen=True)
class PushTarget:
    identifier: str
    is_fid: bool = False


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    # What the client app receives. Values must all be strings for FCM.
    data: dict[str, str]
    category: str
    high_priority: bool
    ttl_seconds: int
    badge: Optional[int] = None
    link: Optional[str] = None
    icon: Optional[str] = None


@dataclass(frozen=True)
class PushResult:
    identifier: str
    status: PushStatus
    error: Optional[str] = None


class PushProvider(ABC):
    @abstractmethod
    def send(self, targets: list[PushTarget], message: PushMessage) -> list[PushResult]:
        """Send to every target, returning one result per target.

        Raises when the whole request fails, such as bad credentials or a network
        error; the caller then retries every target.
        """
