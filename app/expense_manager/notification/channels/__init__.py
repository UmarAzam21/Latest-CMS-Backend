from dataclasses import dataclass
from typing import Optional

from ..model import DeliveryStatus


@dataclass(frozen=True)
class DeliveryOutcome:
    # `pending` means try again: the dispatcher schedules the retry.
    status: DeliveryStatus
    error: Optional[str] = None
    retry_device_ids: Optional[list[int]] = None
