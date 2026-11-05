"""Scheduled notifications. Each run is safe to repeat, because dedupe keys stop a
reminder going out twice, so a missed or doubled schedule tick can't spam anyone.
"""
from calendar import month_name
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ....db import SessionLocal
from ....enums import ExpenseType
from ....models import Card, ExpenseManagerAuth, IncomeExpense
from .. import service
from ..events import NotificationType
from ..model import utcnow
from .celery_app import celery_app

COMMIT_EVERY = 100
CARD_EXPIRY_WINDOW_MONTHS = 1


def _publish_all(db: Session, notifications: list[dict]) -> int:
    published = 0
    for index, notification in enumerate(notifications, start=1):
        if service.publish(db, **notification) is not None:
            published += 1
        if index % COMMIT_EVERY == 0:
            db.commit()
    db.commit()
    return published


@celery_app.task(name="expense_notifications.card_expiry_reminders")
def send_card_expiry_reminders(today: Optional[date] = None) -> int:
    today = today or utcnow().date()
    this_month = today.year * 12 + today.month
    expiry_month = Card.expiry_year * 12 + Card.expiry_month

    with SessionLocal() as db:
        cards = db.query(Card).join(
            ExpenseManagerAuth,
            ExpenseManagerAuth.user_id == Card.user_id,
        ).filter(
            ExpenseManagerAuth.is_active.is_(True),
            expiry_month.between(this_month, this_month + CARD_EXPIRY_WINDOW_MONTHS),
        ).all()
        return _publish_all(
            db,
            [
                {
                    "user_id": card.user_id,
                    "business_id": card.business_id,
                    "notification_type": NotificationType.card_expiring,
                    "context": {
                        "card_id": card.id,
                        "last_4": card.card_number_last_4,
                        "expiry": f"{month_name[card.expiry_month]} {card.expiry_year}",
                    },
                    "dedupe_key": f"card_expiring:{card.id}:{card.expiry_year}-{card.expiry_month}",
                }
                for card in cards
            ],
        )


@celery_app.task(name="expense_notifications.weekly_summaries")
def send_weekly_summaries(today: Optional[date] = None) -> int:
    today = today or utcnow().date()
    year, week, _ = today.isocalendar()

    with SessionLocal() as db:
        rows = db.query(
            IncomeExpense.user_id,
            IncomeExpense.business_id,
            IncomeExpense.type,
            func.sum(IncomeExpense.amount).label("total"),
        ).join(
            ExpenseManagerAuth,
            ExpenseManagerAuth.user_id == IncomeExpense.user_id,
        ).filter(
            ExpenseManagerAuth.is_active.is_(True),
            IncomeExpense.business_id.is_not(None),
            IncomeExpense.type.in_([ExpenseType.income, ExpenseType.expense]),
            IncomeExpense.entry_date >= today - timedelta(days=7),
            IncomeExpense.entry_date < today,
        ).group_by(
            IncomeExpense.user_id,
            IncomeExpense.business_id,
            IncomeExpense.type,
        ).all()

        totals = defaultdict(lambda: {"income": Decimal(0), "expenses": Decimal(0)})
        for row in rows:
            key = "income" if row.type == ExpenseType.income else "expenses"
            totals[(row.user_id, row.business_id)][key] = Decimal(row.total)

        return _publish_all(
            db,
            [
                {
                    "user_id": user_id,
                    "business_id": business_id,
                    "notification_type": NotificationType.weekly_summary,
                    "context": summary,
                    "dedupe_key": f"weekly_summary:{business_id}:{year}-{week}",
                }
                for (user_id, business_id), summary in totals.items()
            ],
        )
