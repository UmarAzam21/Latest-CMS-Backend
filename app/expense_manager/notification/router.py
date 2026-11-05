import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ..auth.dependencies import get_current_expense_user
from . import service
from .channels import in_app
from .channels.websockets import ws_manager
from .config import settings
from .dependencies import authenticate_socket_token
from .events import NotificationCategory, NotificationType
from .model import utcnow
from .schema import (
    DeviceRegisterRequest,
    DeviceResponse,
    MarkReadRequest,
    MarkReadResponse,
    NotificationListResponse,
    PreferenceResponse,
    PreferenceUpdateRequest,
    UnreadCountResponse,
)

router = APIRouter(prefix="/expenses/notifications", tags=["Expense Notifications"])

WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_TOO_MANY_CONNECTIONS = 4429


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    limit: int = Query(25, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    category: Optional[NotificationCategory] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    try:
        items, next_cursor = in_app.list_page(
            db,
            current_user.id,
            current_user.current_business_id,
            limit,
            cursor,
            unread_only,
            category.value if category else None,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor.",
        )
    return {
        "items": items,
        "next_cursor": next_cursor,
        "unread_count": in_app.unread_count(
            db, current_user.id, current_user.current_business_id
        ),
    }


@router.get("/unread-count", response_model=UnreadCountResponse)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    return {
        "unread_count": in_app.unread_count(
            db, current_user.id, current_user.current_business_id
        ),
        "total_unread_count": in_app.total_unread_count(db, current_user.id),
    }


@router.post("/read", response_model=MarkReadResponse)
def mark_read(
    request: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    updated = in_app.mark_read(db, current_user.id, request.ids)
    db.commit()
    service.notify_clients(current_user.id, "notification.read", {"ids": request.ids})
    return {
        "updated": updated,
        "unread_count": in_app.unread_count(
            db, current_user.id, current_user.current_business_id
        ),
    }


@router.post("/read-all", response_model=MarkReadResponse)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    business_id = current_user.current_business_id
    updated = in_app.mark_all_read(db, current_user.id, business_id)
    db.commit()
    service.notify_clients(current_user.id, "notification.read_all", {"business_id": business_id})
    return {"updated": updated, "unread_count": 0}


@router.post("/devices", response_model=DeviceResponse)
def register_device(
    request: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    return service.register_device(
        db,
        current_user.id,
        request.token,
        request.platform,
        request.token_type,
    )


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def unregister_device(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    service.unregister_device(db, current_user.id, device_id)


@router.get("/preferences", response_model=list[PreferenceResponse])
def get_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    return service.list_preferences(db, current_user.id)


@router.put("/preferences/{category}", response_model=list[PreferenceResponse])
def update_preference(
    category: NotificationCategory,
    request: PreferenceUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    return service.update_preference(db, current_user.id, category, request.push_enabled)


@router.post("/test", status_code=status.HTTP_202_ACCEPTED)
def send_test_notification(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    """Send one notification through every channel, to check a client integration."""
    notification = service.publish(
        db,
        user_id=current_user.id,
        notification_type=NotificationType.test,
        # One per minute per user: this reaches real devices.
        dedupe_key=f"test:{utcnow():%Y%m%d%H%M}",
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A test notification was already sent. Try again in a minute.",
        )
    db.commit()
    return {"notification_id": notification.id}


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    if not in_app.delete(db, current_user.id, notification_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    db.commit()
    service.notify_clients(current_user.id, "notification.deleted", {"id": notification_id})


async def _authenticate_socket(websocket: WebSocket):
    # The token comes in the first message rather than the URL, because browsers
    # can't set WebSocket headers and URLs end up in access logs.
    try:
        message = json.loads(
            await asyncio.wait_for(
                websocket.receive_text(),
                settings.ws_auth_timeout_seconds,
            )
        )
        token = message["token"]
    except (asyncio.TimeoutError, ValueError, KeyError, TypeError):
        return None
    if message.get("type") != "auth" or not isinstance(token, str):
        return None
    return await run_in_threadpool(authenticate_socket_token, token)


@router.websocket("/ws")
async def notifications_socket(websocket: WebSocket):
    await websocket.accept()
    try:
        identity = await _authenticate_socket(websocket)
        if identity is None:
            await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        if not ws_manager.connect(identity.user_id, websocket):
            await websocket.close(code=WS_CLOSE_TOO_MANY_CONNECTIONS)
            return

        try:
            await websocket.send_json({"type": "connection.ready", "data": {}})
            while True:
                remaining = identity.expires_at - time.time()
                if remaining <= 0:
                    await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
                    return
                try:
                    text = await asyncio.wait_for(websocket.receive_text(), remaining)
                except (asyncio.TimeoutError, KeyError):
                    # KeyError is a binary frame, which this protocol doesn't use.
                    continue
                if text == "ping":
                    await websocket.send_text("pong")
        finally:
            ws_manager.disconnect(identity.user_id, websocket)
    except WebSocketDisconnect:
        pass
