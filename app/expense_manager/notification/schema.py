from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .events import NotificationCategory
from .model import DevicePlatform, DeviceTokenType


class NotificationResponse(BaseModel):
    id: str
    type: str
    category: NotificationCategory
    priority: str
    title: str
    body: str
    data: Optional[dict[str, Any]] = None
    action_url: Optional[str] = None
    business_id: Optional[int] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    next_cursor: Optional[str] = None
    unread_count: int


class UnreadCountResponse(BaseModel):
    # Active business plus account-wide notifications: the in-app badge.
    unread_count: int
    # Across every business on the login: the app icon badge.
    total_unread_count: int


class MarkReadRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)


class MarkReadResponse(BaseModel):
    updated: int
    unread_count: int


class DeviceRegisterRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=4096)
    platform: DevicePlatform
    token_type: DeviceTokenType = DeviceTokenType.fcm_token


class DeviceResponse(BaseModel):
    id: int
    platform: DevicePlatform
    is_active: bool
    last_seen_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PreferenceResponse(BaseModel):
    category: NotificationCategory
    label: str
    push_enabled: bool
    locked: bool


class PreferenceUpdateRequest(BaseModel):
    push_enabled: bool
