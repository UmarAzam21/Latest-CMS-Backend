"""The inbox. Mobile and web read the same rows, so read state stays in sync."""
import base64
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..model import ExpenseNotification, utcnow


def _business_scope(business_id: Optional[int]):
    if business_id is None:
        return ExpenseNotification.business_id.is_(None)
    return or_(
        ExpenseNotification.business_id == business_id,
        ExpenseNotification.business_id.is_(None),
    )


def encode_cursor(notification: ExpenseNotification) -> str:
    raw = f"{notification.created_at.isoformat()}|{notification.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Raises ValueError for anything that isn't a cursor this API issued."""
    created_at, notification_id = base64.urlsafe_b64decode(cursor).decode().split("|")
    return datetime.fromisoformat(created_at), notification_id


def list_page(
    db: Session,
    user_id: int,
    business_id: Optional[int],
    limit: int,
    cursor: Optional[str] = None,
    unread_only: bool = False,
    category: Optional[str] = None,
) -> tuple[list[ExpenseNotification], Optional[str]]:
    # Keyset pagination: new arrivals can't shift or repeat rows between pages.
    query = db.query(ExpenseNotification).filter(
        ExpenseNotification.user_id == user_id,
        _business_scope(business_id),
    )
    if unread_only:
        query = query.filter(ExpenseNotification.read_at.is_(None))
    if category is not None:
        query = query.filter(ExpenseNotification.category == category)
    if cursor is not None:
        created_at, notification_id = decode_cursor(cursor)
        query = query.filter(
            or_(
                ExpenseNotification.created_at < created_at,
                and_(
                    ExpenseNotification.created_at == created_at,
                    ExpenseNotification.id < notification_id,
                ),
            )
        )

    rows = query.order_by(
        ExpenseNotification.created_at.desc(),
        ExpenseNotification.id.desc(),
    ).limit(limit + 1).all()
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1]) if len(rows) > limit else None
    return items, next_cursor


def unread_count(db: Session, user_id: int, business_id: Optional[int]) -> int:
    return db.query(func.count(ExpenseNotification.id)).filter(
        ExpenseNotification.user_id == user_id,
        ExpenseNotification.read_at.is_(None),
        _business_scope(business_id),
    ).scalar() or 0


def total_unread_count(db: Session, user_id: int) -> int:
    return db.query(func.count(ExpenseNotification.id)).filter(
        ExpenseNotification.user_id == user_id,
        ExpenseNotification.read_at.is_(None),
    ).scalar() or 0


def mark_read(db: Session, user_id: int, notification_ids: list[str]) -> int:
    return db.query(ExpenseNotification).filter(
        ExpenseNotification.user_id == user_id,
        ExpenseNotification.id.in_(notification_ids),
        ExpenseNotification.read_at.is_(None),
    ).update({ExpenseNotification.read_at: utcnow()}, synchronize_session=False)


def mark_all_read(db: Session, user_id: int, business_id: Optional[int]) -> int:
    return db.query(ExpenseNotification).filter(
        ExpenseNotification.user_id == user_id,
        ExpenseNotification.read_at.is_(None),
        _business_scope(business_id),
    ).update({ExpenseNotification.read_at: utcnow()}, synchronize_session=False)


def delete(db: Session, user_id: int, notification_id: str) -> bool:
    notification = db.query(ExpenseNotification).filter(
        ExpenseNotification.id == notification_id,
        ExpenseNotification.user_id == user_id,
    ).first()
    if notification is None:
        return False
    db.delete(notification)
    return True
