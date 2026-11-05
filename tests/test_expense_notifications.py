import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import messaging
from firebase_admin._messaging_encoder import Message as EncodedMessage
from firebase_admin._messaging_encoder import MessageEncoder
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app import main
from app.db import Base, get_db
from app.enums import ExpenseType
from app.expense_manager.auth import service as auth_service
from app.expense_manager.auth.dependencies import get_current_expense_user
from app.expense_manager.notification import dependencies as notification_dependencies
from app.expense_manager.notification import service
from app.expense_manager.notification.channels import in_app, push, websockets
from app.expense_manager.notification.channels.websockets import ws_manager
from app.expense_manager.notification.config import settings
from app.expense_manager.notification.events import (
    CATEGORY_POLICIES,
    NOTIFICATION_SPECS,
    NotificationCategory,
    NotificationType,
)
from app.expense_manager.notification.model import (
    DeliveryStatus,
    ExpenseNotification,
    ExpenseNotificationDelivery,
    ExpenseNotificationDevice,
    ExpenseNotificationPreference,
    utcnow,
)
from app.expense_manager.notification.provider import firebase_provider
from app.expense_manager.notification.provider.base_provider import (
    PushMessage,
    PushProvider,
    PushResult,
    PushStatus,
    PushTarget,
)
from app.expense_manager.notification.router import router as notification_router
from app.expense_manager.notification.worker import (
    celery_app as celery_module,
    notification_tasks,
    outbox_dispatcher,
    reminder_tasks,
    scheduler,
)
from app.expense_manager.router import (
    create_card,
    create_expense,
    transfer_balance_between_cards,
)
from app.models import (
    Card,
    ExpenseCategoryOption,
    ExpenseManagerAuth,
    ExpenseManagerBusiness,
    IncomeExpense,
    User,
)
from app.schema import ExpenseManagerRegisterRequest


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Cascades must behave as they do on MariaDB.
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(outbox_dispatcher, "SessionLocal", factory)
    monkeypatch.setattr(reminder_tasks, "SessionLocal", factory)
    monkeypatch.setattr(notification_dependencies, "SessionLocal", factory)
    monkeypatch.setattr(outbox_dispatcher, "is_backend_blocked", lambda: False)
    monkeypatch.setattr(settings, "redis_url", None)
    # bcrypt is deliberately slow, and hashing isn't what these tests cover.
    monkeypatch.setattr(auth_service, "hash_password", lambda value: value)
    monkeypatch.setattr(auth_service, "verify_password", lambda value, stored: value == stored)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db(engine):
    with Session(engine, autoflush=False) as session:
        yield session


def register(db: Session, phone: str = "03001234567") -> ExpenseManagerAuth:
    return auth_service.register_account(
        db,
        ExpenseManagerRegisterRequest(phone_number=phone, business_name="Main", pin="1234"),
    )


def business_of(db: Session, account: ExpenseManagerAuth) -> ExpenseManagerBusiness:
    return db.query(ExpenseManagerBusiness).filter(
        ExpenseManagerBusiness.account_id == account.id
    ).first()


def as_expense_user(db: Session, account: ExpenseManagerAuth) -> User:
    user = db.get(User, account.user_id)
    user.current_business_id = business_of(db, account).id
    for name in ("Food", "Other"):
        db.add(
            ExpenseCategoryOption(
                user_id=user.id,
                business_id=user.current_business_id,
                type=ExpenseType.expense,
                name=name,
                color="#000000",
            )
        )
    db.commit()
    return user


def add_device(db: Session, user_id: int, token: str = "device-token-0000000001") -> ExpenseNotificationDevice:
    return service.register_device(db, user_id, token, service.DevicePlatform.android)


def publish_committed(db: Session, user_id: int, notification_type=NotificationType.pin_changed, **kwargs):
    notification = service.publish(
        db,
        user_id=user_id,
        notification_type=notification_type,
        **kwargs,
    )
    db.commit()
    return notification


def deliveries(db: Session, notification_id: str) -> dict[str, ExpenseNotificationDelivery]:
    db.expire_all()
    return {
        row.channel: row
        for row in db.query(ExpenseNotificationDelivery).filter(
            ExpenseNotificationDelivery.notification_id == notification_id
        )
    }


class FakeProvider(PushProvider):
    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.calls = []

    def send(self, targets, message):
        tokens = [target.identifier for target in targets]
        self.calls.append((tokens, message))
        self.targets = targets
        return [
            PushResult(token, self.statuses.get(token, PushStatus.delivered), "boom")
            for token in tokens
        ]


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(push, "get_push_provider", lambda: fake)
    return fake


# ---- Publishing ----


def test_every_type_has_a_spec_and_every_category_a_policy():
    assert set(NOTIFICATION_SPECS) == set(NotificationType)
    assert set(CATEGORY_POLICIES) == set(NotificationCategory)


def test_expense_creates_inbox_row_and_syncs_clients_without_pushing(db: Session):
    account = register(db)
    user = as_expense_user(db, account)
    card = asyncio.run(
        create_card("1234", None, Decimal("500"), None, None, None, db, user)
    )
    response = asyncio.run(
        create_expense(
            "Lunch", "Food", Decimal("120.50"), date(2026, 11, 1), card.id, None, db, user
        )
    )

    notification = db.query(ExpenseNotification).filter(
        ExpenseNotification.type == NotificationType.expense_created.value
    ).one()
    assert notification.business_id == user.current_business_id
    assert notification.body == "PKR 120.50 for Lunch"
    assert notification.action_url == f"/transactions/{response.id}"
    assert notification.data["amount"] == "120.50"
    assert set(deliveries(db, notification.id)) == {"websocket"}


def test_notification_rolls_back_with_the_callers_transaction(db: Session):
    account = register(db)
    account.business_name = "Renamed"
    service.publish(db, user_id=account.user_id, notification_type=NotificationType.pin_changed)
    db.rollback()

    assert account.business_name == "Main"
    assert db.query(ExpenseNotification).count() == 0
    assert db.query(ExpenseNotificationDelivery).count() == 0


def test_broken_notification_does_not_fail_the_caller(db: Session, caplog):
    account = register(db)
    account.business_name = "Renamed"
    # Missing template values raise inside publish.
    result = service.publish(
        db,
        user_id=account.user_id,
        notification_type=NotificationType.card_added,
    )
    db.commit()
    db.expire_all()

    assert result is None
    assert account.business_name == "Renamed"
    assert db.query(ExpenseNotification).count() == 0
    assert "Could not queue" in caplog.text


def test_failure_in_the_callers_pending_work_still_reaches_the_caller(db: Session):
    account = register(db)
    db.add(User(username=account.phone_number, password_hash="x"))

    with pytest.raises(Exception, match="UNIQUE"):
        service.publish(db, user_id=account.user_id, notification_type=NotificationType.pin_changed)
    db.rollback()


def test_dedupe_key_sends_only_once(db: Session):
    account = register(db)
    first = publish_committed(db, account.user_id, dedupe_key="once")
    second = publish_committed(db, account.user_id, dedupe_key="once")

    assert first is not None
    assert second is None
    assert db.query(ExpenseNotification).count() == 1


def test_dedupe_race_is_quiet(db: Session, caplog, monkeypatch):
    account = register(db)
    publish_committed(db, account.user_id, dedupe_key="race")

    # Simulate a concurrent insert landing after the existence check.
    monkeypatch.setattr(
        service,
        "_create",
        lambda *args: db.add(
            ExpenseNotification(
                user_id=account.user_id,
                type="x",
                category="system",
                title="t",
                body="b",
                dedupe_key="race",
            )
        ),
    )
    assert service.publish(
        db,
        user_id=account.user_id,
        notification_type=NotificationType.pin_changed,
        dedupe_key="race",
    ) is None
    assert "Could not queue" not in caplog.text
    db.commit()
    assert db.query(ExpenseNotification).count() == 1


def test_transaction_push_is_opt_in(db: Session):
    account = register(db)
    service.update_preference(db, account.user_id, NotificationCategory.transactions, True)
    notification = publish_committed(
        db,
        account.user_id,
        NotificationType.test,
    )
    assert set(deliveries(db, notification.id)) == {"websocket", "push"}

    opted_in = publish_committed(
        db,
        account.user_id,
        NotificationType.card_added,
        context={"card_id": 1, "last_4": "1234"},
    )
    assert set(deliveries(db, opted_in.id)) == {"websocket", "push"}


def test_disabled_category_skips_push_but_keeps_inbox(db: Session):
    account = register(db)
    service.update_preference(db, account.user_id, NotificationCategory.system, False)
    notification = publish_committed(db, account.user_id, NotificationType.test)

    assert set(deliveries(db, notification.id)) == {"websocket"}


def test_low_balance_alert_fires_only_when_crossing(db: Session):
    account = register(db)
    user = as_expense_user(db, account)
    card = asyncio.run(
        create_card("1234", None, Decimal("100"), None, None, None, db, user)
    )

    def spend(amount: str) -> None:
        asyncio.run(
            create_expense("Item", "Other", Decimal(amount), date(2026, 11, 1), card.id, None, db, user)
        )

    def alerts() -> int:
        return db.query(ExpenseNotification).filter(
            ExpenseNotification.type == NotificationType.low_balance.value
        ).count()

    spend("60")
    assert alerts() == 0
    spend("50")
    assert alerts() == 1
    spend("10")
    assert alerts() == 1


def test_transfer_notifies_and_can_alert_on_low_balance(db: Session):
    account = register(db)
    user = as_expense_user(db, account)
    source = asyncio.run(create_card("1111", None, Decimal("100"), None, None, None, db, user))
    target = asyncio.run(create_card("2222", None, Decimal("0"), None, None, None, db, user))

    asyncio.run(transfer_balance_between_cards(source.id, target.id, Decimal("100"), db, user))

    types = {row.type for row in db.query(ExpenseNotification)}
    assert {"transfer_completed", "low_balance"} <= types
    transfer = db.query(ExpenseNotification).filter(
        ExpenseNotification.type == "transfer_completed"
    ).one()
    assert transfer.body == "PKR 100.00 moved from card ending 1111 to card ending 2222"


def test_pin_change_sends_an_account_wide_security_alert(db: Session):
    account = register(db)
    auth_service.change_pin(db, account, "1234", "4321")

    notification = db.query(ExpenseNotification).one()
    assert notification.type == NotificationType.pin_changed.value
    assert notification.business_id is None
    assert set(deliveries(db, notification.id)) == {"websocket", "push"}


def test_lockout_sends_a_security_alert(db: Session):
    account = register(db)
    for _ in range(auth_service.MAX_FAILED_PIN_ATTEMPTS):
        with pytest.raises(HTTPException):
            auth_service.authenticate(db, account.phone_number, "0000")

    notification = db.query(ExpenseNotification).one()
    assert notification.type == NotificationType.account_locked.value
    assert "15 minutes" in notification.body


# ---- Outbox ----


def test_claim_hides_rows_until_the_visibility_window_ends(db: Session):
    account = register(db)
    notification = publish_committed(db, account.user_id)

    first = outbox_dispatcher.claim_due(db)
    assert len(first) == 2
    assert outbox_dispatcher.claim_due(db) == []

    for row in deliveries(db, notification.id).values():
        assert row.attempts == 1
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert sorted(outbox_dispatcher.claim_due(db)) == sorted(first)


def test_claim_respects_the_batch_limit(db: Session):
    account = register(db)
    publish_committed(db, account.user_id)

    assert len(outbox_dispatcher.claim_due(db, limit=1)) == 1
    assert len(outbox_dispatcher.claim_due(db, limit=5)) == 1


def test_delivery_lost_too_many_times_is_marked_dead(db: Session):
    account = register(db)
    notification = publish_committed(db, account.user_id)
    for row in deliveries(db, notification.id).values():
        row.attempts = settings.max_attempts
    db.commit()

    assert outbox_dispatcher.claim_due(db) == []
    assert {row.status for row in deliveries(db, notification.id).values()} == {"dead"}


def test_end_to_end_push_and_retention_of_state(db: Session, provider):
    account = register(db)
    add_device(db, account.user_id)
    notification = publish_committed(db, account.user_id)

    assert outbox_dispatcher.dispatch_due() == 2

    rows = deliveries(db, notification.id)
    assert rows["push"].status == "sent"
    assert rows["push"].sent_at is not None
    # Nothing in this process owns a socket, and that must be recorded honestly.
    assert rows["websocket"].status == "skipped"
    tokens, message = provider.calls[0]
    assert tokens == ["device-token-0000000001"]
    assert message.title == "PIN changed"
    assert message.high_priority is True
    assert message.badge == 1
    assert message.data["notification_id"] == notification.id
    assert outbox_dispatcher.dispatch_due() == 0


def test_dispatch_does_nothing_while_backend_is_blocked(db: Session, monkeypatch):
    account = register(db)
    publish_committed(db, account.user_id)
    monkeypatch.setattr(outbox_dispatcher, "is_backend_blocked", lambda: True)

    assert outbox_dispatcher.dispatch_due() == 0


def test_delivery_is_not_repeated_once_finished(db: Session, provider):
    account = register(db)
    add_device(db, account.user_id)
    notification = publish_committed(db, account.user_id)
    outbox_dispatcher.dispatch_due()

    outbox_dispatcher.deliver(deliveries(db, notification.id)["push"].id)

    assert len(provider.calls) == 1


def push_delivery(db: Session, notification_id: str) -> ExpenseNotificationDelivery:
    return deliveries(db, notification_id)["push"]


def claim_and_deliver_push(db: Session, notification_id: str) -> None:
    delivery = push_delivery(db, notification_id)
    delivery.attempts += 1
    db.commit()
    outbox_dispatcher.deliver(delivery.id)


def test_partial_failure_retries_only_the_failed_device(db: Session, provider):
    account = register(db)
    good = add_device(db, account.user_id, "good-token-00000000000")
    flaky = add_device(db, account.user_id, "flaky-token-0000000000")
    provider.statuses = {flaky.token: PushStatus.retryable}
    notification = publish_committed(db, account.user_id)

    claim_and_deliver_push(db, notification.id)

    delivery = push_delivery(db, notification.id)
    assert delivery.status == "pending"
    assert delivery.pending_device_ids == [flaky.id]
    assert delivery.next_attempt_at >= utcnow() + timedelta(seconds=settings.retry_base_seconds - 1)

    provider.statuses = {}
    claim_and_deliver_push(db, notification.id)

    assert provider.calls[1][0] == [flaky.token]
    assert good.token not in provider.calls[1][0]
    assert push_delivery(db, notification.id).status == "sent"


def test_retry_gives_up_after_max_attempts(db: Session, provider):
    account = register(db)
    device = add_device(db, account.user_id)
    provider.statuses = {device.token: PushStatus.retryable}
    notification = publish_committed(db, account.user_id)

    for _ in range(settings.max_attempts):
        assert push_delivery(db, notification.id).status == "pending"
        claim_and_deliver_push(db, notification.id)

    delivery = push_delivery(db, notification.id)
    assert delivery.status == "dead"
    assert delivery.last_error == "boom"


def test_provider_outage_keeps_the_devices_still_owed(db: Session, monkeypatch):
    account = register(db)
    device = add_device(db, account.user_id)

    class Down(PushProvider):
        def send(self, targets, message):
            raise RuntimeError("FCM unavailable")

    monkeypatch.setattr(push, "get_push_provider", lambda: Down())
    notification = publish_committed(db, account.user_id)
    delivery = push_delivery(db, notification.id)
    delivery.pending_device_ids = [device.id]
    db.commit()

    claim_and_deliver_push(db, notification.id)

    delivery = push_delivery(db, notification.id)
    assert delivery.status == "pending"
    assert delivery.pending_device_ids == [device.id]
    assert delivery.last_error == "FCM unavailable"


def test_unregistered_token_deactivates_the_device(db: Session, provider):
    account = register(db)
    device = add_device(db, account.user_id)
    provider.statuses = {device.token: PushStatus.invalid_token}
    notification = publish_committed(db, account.user_id)

    claim_and_deliver_push(db, notification.id)

    db.expire_all()
    assert device.is_active is False
    assert push_delivery(db, notification.id).status == "skipped"


def test_rejected_message_is_not_retried(db: Session, provider):
    account = register(db)
    device = add_device(db, account.user_id)
    provider.statuses = {device.token: PushStatus.rejected}
    notification = publish_committed(db, account.user_id)

    claim_and_deliver_push(db, notification.id)

    assert push_delivery(db, notification.id).status == "dead"


def test_push_is_skipped_without_devices(db: Session, provider):
    account = register(db)
    notification = publish_committed(db, account.user_id)

    claim_and_deliver_push(db, notification.id)

    assert push_delivery(db, notification.id).status == "skipped"
    assert provider.calls == []


def test_push_is_skipped_when_firebase_is_not_configured(db: Session, monkeypatch):
    monkeypatch.setattr(settings, "firebase_credentials_path", None)
    monkeypatch.setattr(settings, "firebase_credentials_json", None)
    push.get_push_provider.cache_clear()
    account = register(db)
    add_device(db, account.user_id)
    notification = publish_committed(db, account.user_id)

    claim_and_deliver_push(db, notification.id)
    push.get_push_provider.cache_clear()

    delivery = push_delivery(db, notification.id)
    assert delivery.status == "skipped"
    assert delivery.last_error == "Push provider is not configured."


def test_deliveries_too_old_to_matter_are_dropped(db: Session, provider):
    account = register(db)
    add_device(db, account.user_id)
    notification = publish_committed(db, account.user_id)
    notification.created_at = utcnow() - timedelta(hours=settings.delivery_max_age_hours + 1)
    db.commit()

    claim_and_deliver_push(db, notification.id)

    assert push_delivery(db, notification.id).status == "skipped"
    assert provider.calls == []


def test_deleted_notification_takes_its_pending_deliveries_with_it(db: Session):
    account = register(db)
    notification = publish_committed(db, account.user_id)

    assert in_app.delete(db, account.user_id, notification.id)
    db.commit()

    assert db.query(ExpenseNotificationDelivery).count() == 0


# ---- Reminders ----


def add_card(db: Session, account: ExpenseManagerAuth, last_4: str, year: int, month: int) -> Card:
    card = Card(
        user_id=account.user_id,
        business_id=business_of(db, account).id,
        card_number_last_4=last_4,
        balance=Decimal("0"),
        expiry_year=year,
        expiry_month=month,
    )
    db.add(card)
    db.commit()
    return card


def test_card_expiry_reminder_is_sent_once_for_cards_in_the_window(db: Session):
    account = register(db)
    expiring = add_card(db, account, "1111", 2026, 12)
    add_card(db, account, "2222", 2027, 6)
    add_card(db, account, "3333", 2026, 10)

    today = date(2026, 11, 20)
    assert reminder_tasks.send_card_expiry_reminders(today) == 1
    assert reminder_tasks.send_card_expiry_reminders(today + timedelta(days=1)) == 0

    notification = db.query(ExpenseNotification).one()
    assert notification.body == "Card ending 1111 expires December 2026"
    assert notification.action_url == f"/cards/{expiring.id}"


def test_card_expiry_window_crosses_the_year_boundary(db: Session):
    account = register(db)
    add_card(db, account, "1111", 2027, 1)

    assert reminder_tasks.send_card_expiry_reminders(date(2026, 12, 15)) == 1


def test_reminders_skip_deactivated_accounts(db: Session):
    account = register(db)
    add_card(db, account, "1111", 2026, 12)
    account.is_active = False
    db.commit()

    assert reminder_tasks.send_card_expiry_reminders(date(2026, 11, 20)) == 0


def test_weekly_summary_totals_each_business_separately(db: Session):
    account = register(db)
    first = business_of(db, account)
    second = ExpenseManagerBusiness(account_id=account.id, name="Shop", currency="USD")
    db.add(second)
    db.flush()

    def entry(business, entry_type, amount, entry_date):
        db.add(
            IncomeExpense(
                user_id=account.user_id,
                business_id=business.id,
                type=entry_type,
                subject="x",
                amount=Decimal(amount),
                entry_date=entry_date,
            )
        )

    monday = date(2026, 11, 9)
    entry(first, ExpenseType.income, "1000", date(2026, 11, 5))
    entry(first, ExpenseType.expense, "250.50", date(2026, 11, 8))
    entry(first, ExpenseType.expense, "9999", date(2026, 11, 1))
    entry(first, ExpenseType.debt, "7777", date(2026, 11, 5))
    entry(second, ExpenseType.expense, "20", date(2026, 11, 6))
    db.commit()

    assert reminder_tasks.send_weekly_summaries(monday) == 2
    assert reminder_tasks.send_weekly_summaries(monday) == 0

    bodies = {
        row.business_id: row.body
        for row in db.query(ExpenseNotification)
    }
    assert bodies[first.id] == "Last 7 days: PKR 1,000.00 income, PKR 250.50 expenses"
    assert bodies[second.id] == "Last 7 days: USD 0.00 income, USD 20.00 expenses"


# ---- Retention ----


def test_purge_applies_each_retention_rule(db: Session):
    account = register(db)
    now = utcnow()

    old_read = publish_committed(db, account.user_id)
    old_read.created_at = now - timedelta(days=settings.read_retention_days + 1)
    old_read.read_at = now
    kept_unread = publish_committed(db, account.user_id, NotificationType.test)
    kept_unread.created_at = now - timedelta(days=settings.read_retention_days + 1)
    old_unread = publish_committed(db, account.user_id, NotificationType.test, dedupe_key="old")
    old_unread.created_at = now - timedelta(days=settings.unread_retention_days + 1)
    stale = add_device(db, account.user_id)
    stale.last_seen_at = now - timedelta(days=settings.device_stale_days + 1)
    db.commit()
    for row in deliveries(db, kept_unread.id).values():
        row.status = "sent"
        row.created_at = now - timedelta(days=settings.delivery_retention_days + 1)
    db.commit()

    result = outbox_dispatcher.purge_expired()

    assert result == {"deliveries": 2, "notifications": 2, "devices_deactivated": 1}
    db.expire_all()
    assert [row.id for row in db.query(ExpenseNotification)] == [kept_unread.id]


def test_purge_keeps_pending_deliveries(db: Session):
    account = register(db)
    notification = publish_committed(db, account.user_id)
    for row in deliveries(db, notification.id).values():
        row.created_at = utcnow() - timedelta(days=settings.delivery_retention_days + 1)
    db.commit()

    assert outbox_dispatcher.purge_expired()["deliveries"] == 0


# ---- HTTP API ----


@pytest.fixture
def api(engine):
    @asynccontextmanager
    async def lifespan(_app):
        await ws_manager.start()
        yield
        await ws_manager.stop()

    application = FastAPI(lifespan=lifespan)
    application.include_router(notification_router, prefix="/api")

    factory = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    return application


def client_for(application: FastAPI, db: Session, account: ExpenseManagerAuth, business_id=None):
    token = auth_service.create_token_for(
        account,
        business_id or business_of(db, account).id,
    )
    client = TestClient(application, headers={"Authorization": f"Bearer {token}"})
    client.token = token
    return client


URL = "/api/expenses/notifications"


def test_api_requires_authentication(api):
    assert TestClient(api).get(URL).status_code == 401


def test_inbox_is_scoped_to_user_and_active_business(api, db: Session):
    account = register(db)
    other_account = register(db, "03009999999")
    first = business_of(db, account)
    second = ExpenseManagerBusiness(account_id=account.id, name="Shop")
    db.add(second)
    db.commit()

    in_first = publish_committed(db, account.user_id, NotificationType.test, business_id=first.id)
    publish_committed(
        db, account.user_id, NotificationType.test, business_id=second.id, dedupe_key="s"
    )
    account_wide = publish_committed(db, account.user_id)
    publish_committed(db, other_account.user_id)

    response = client_for(api, db, account, first.id).get(URL)

    assert response.status_code == 200
    body = response.json()
    assert {item["id"] for item in body["items"]} == {in_first.id, account_wide.id}
    assert body["unread_count"] == 2

    counts = client_for(api, db, account, first.id).get(f"{URL}/unread-count").json()
    assert counts == {"unread_count": 2, "total_unread_count": 3}


def test_pagination_walks_every_row_exactly_once(api, db: Session):
    account = register(db)
    created_at = utcnow().replace(microsecond=0)
    expected = set()
    for index in range(7):
        notification = publish_committed(
            db, account.user_id, NotificationType.test, dedupe_key=str(index)
        )
        # MariaDB DATETIME has one-second precision, so ties are the normal case.
        notification.created_at = created_at
        expected.add(notification.id)
    db.commit()

    client = client_for(api, db, account)
    seen = []
    cursor = None
    while True:
        params = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = client.get(URL, params=params).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 7
    assert set(seen) == expected


def test_garbage_cursor_is_a_client_error(api, db: Session):
    account = register(db)
    client = client_for(api, db, account)

    for cursor in ("not-a-cursor", "@@@", "YWJj"):
        assert client.get(URL, params={"cursor": cursor}).status_code == 422


def test_inbox_filters(api, db: Session):
    account = register(db)
    security = publish_committed(db, account.user_id)
    publish_committed(db, account.user_id, NotificationType.test)
    read = publish_committed(db, account.user_id, NotificationType.test, dedupe_key="r")
    read.read_at = utcnow()
    db.commit()
    client = client_for(api, db, account)

    assert len(client.get(URL, params={"unread_only": True}).json()["items"]) == 2
    items = client.get(URL, params={"category": "security"}).json()["items"]
    assert [item["id"] for item in items] == [security.id]
    assert client.get(URL, params={"category": "nope"}).status_code == 422


def test_mark_read_and_delete_cannot_touch_another_users_rows(api, db: Session):
    owner = register(db)
    stranger = register(db, "03009999999")
    notification = publish_committed(db, owner.user_id)
    client = client_for(api, db, stranger)

    assert client.post(f"{URL}/read", json={"ids": [notification.id]}).json()["updated"] == 0
    assert client.delete(f"{URL}/{notification.id}").status_code == 404

    db.expire_all()
    assert db.get(ExpenseNotification, notification.id).read_at is None


def test_read_state_is_shared_by_every_client(api, db: Session):
    account = register(db)
    notification = publish_committed(db, account.user_id)
    publish_committed(db, account.user_id, NotificationType.test)
    mobile = client_for(api, db, account)
    web = client_for(api, db, account)

    response = mobile.post(f"{URL}/read", json={"ids": [notification.id]})

    assert response.json() == {"updated": 1, "unread_count": 1}
    assert web.get(f"{URL}/unread-count").json()["unread_count"] == 1
    item = next(
        row for row in web.get(URL).json()["items"] if row["id"] == notification.id
    )
    assert item["is_read"] is True


def test_read_all_only_covers_the_active_business(api, db: Session):
    account = register(db)
    first = business_of(db, account)
    second = ExpenseManagerBusiness(account_id=account.id, name="Shop")
    db.add(second)
    db.commit()
    publish_committed(db, account.user_id, NotificationType.test, business_id=first.id)
    publish_committed(
        db, account.user_id, NotificationType.test, business_id=second.id, dedupe_key="s"
    )

    client = client_for(api, db, account, first.id)
    assert client.post(f"{URL}/read-all").json() == {"updated": 1, "unread_count": 0}
    assert client.get(f"{URL}/unread-count").json()["total_unread_count"] == 1


def test_device_response_never_exposes_the_token(api, db: Session):
    account = register(db)
    client = client_for(api, db, account)

    response = client.post(
        f"{URL}/devices",
        json={"token": "secret-token-0000000000", "platform": "web"},
    )

    assert response.status_code == 200
    assert "secret-token" not in response.text
    assert response.json()["platform"] == "web"


def test_device_registration_validates_input(api, db: Session):
    account = register(db)
    client = client_for(api, db, account)

    assert client.post(
        f"{URL}/devices", json={"token": "short", "platform": "web"}
    ).status_code == 422
    assert client.post(
        f"{URL}/devices", json={"token": "x" * 40, "platform": "symbian"}
    ).status_code == 422


def test_token_follows_the_user_who_signed_in_last(api, db: Session):
    first = register(db)
    second = register(db, "03009999999")
    body = {"token": "shared-phone-token-000000", "platform": "android"}

    first_id = client_for(api, db, first).post(f"{URL}/devices", json=body).json()["id"]
    second_id = client_for(api, db, second).post(f"{URL}/devices", json=body).json()["id"]

    assert first_id == second_id
    db.expire_all()
    device = db.get(ExpenseNotificationDevice, first_id)
    assert device.user_id == second.user_id
    assert db.query(ExpenseNotificationDevice).count() == 1


def test_device_limit_retires_the_least_recently_seen(db: Session, monkeypatch):
    monkeypatch.setattr(settings, "max_devices_per_user", 2)
    account = register(db)
    oldest = add_device(db, account.user_id, "oldest-token-0000000000")
    oldest.last_seen_at = utcnow() - timedelta(days=5)
    db.commit()
    add_device(db, account.user_id, "middle-token-0000000000")
    add_device(db, account.user_id, "newest-token-0000000000")

    db.expire_all()
    assert oldest.is_active is False
    assert db.query(ExpenseNotificationDevice).filter(
        ExpenseNotificationDevice.is_active.is_(True)
    ).count() == 2


def test_unregister_device(api, db: Session):
    owner = register(db)
    stranger = register(db, "03009999999")
    device = add_device(db, owner.user_id)

    assert client_for(api, db, stranger).delete(f"{URL}/devices/{device.id}").status_code == 404
    assert client_for(api, db, owner).delete(f"{URL}/devices/{device.id}").status_code == 204
    db.expire_all()
    assert device.is_active is False


def test_preferences(api, db: Session):
    account = register(db)
    client = client_for(api, db, account)

    preferences = {row["category"]: row for row in client.get(f"{URL}/preferences").json()}
    assert preferences["transactions"]["push_enabled"] is False
    assert preferences["security"] == {
        "category": "security",
        "label": "Security",
        "push_enabled": True,
        "locked": True,
    }

    response = client.put(f"{URL}/preferences/transactions", json={"push_enabled": True})
    assert response.status_code == 200
    updated = {row["category"]: row for row in response.json()}
    assert updated["transactions"]["push_enabled"] is True

    client.put(f"{URL}/preferences/transactions", json={"push_enabled": False})
    assert db.query(ExpenseNotificationPreference).count() == 1


def test_security_notifications_cannot_be_disabled(api, db: Session):
    account = register(db)
    client = client_for(api, db, account)

    response = client.put(f"{URL}/preferences/security", json={"push_enabled": False})

    assert response.status_code == 409


def test_test_endpoint_is_rate_limited(api, db: Session):
    account = register(db)
    client = client_for(api, db, account)

    first = client.post(f"{URL}/test")
    second = client.post(f"{URL}/test")

    assert first.status_code == 202
    assert first.json()["notification_id"]
    assert second.status_code == 429


# ---- WebSocket ----


def test_socket_receives_live_notifications_and_read_sync(api, db: Session):
    account = register(db)
    with TestClient(api) as client:
        token = auth_service.create_token_for(account, business_of(db, account).id)
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            assert socket.receive_json()["type"] == "connection.ready"

            notification = publish_committed(db, account.user_id)
            outbox_dispatcher.dispatch_due()

            event = socket.receive_json()
            assert event["type"] == "notification.created"
            assert event["data"]["id"] == notification.id
            assert event["data"]["title"] == "PIN changed"
            assert event["data"]["is_read"] is False
            assert deliveries(db, notification.id)["websocket"].status == "sent"

            response = client.post(
                f"{URL}/read",
                json={"ids": [notification.id]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert socket.receive_json() == {
                "type": "notification.read",
                "data": {"ids": [notification.id]},
            }

            socket.send_text("ping")
            assert socket.receive_text() == "pong"


def test_socket_only_reaches_its_owner(api, db: Session):
    owner = register(db)
    stranger = register(db, "03009999999")
    with TestClient(api) as client:
        token = auth_service.create_token_for(stranger, business_of(db, stranger).id)
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            socket.receive_json()

            publish_committed(db, owner.user_id)
            outbox_dispatcher.dispatch_due()
            own = publish_committed(db, stranger.user_id)
            outbox_dispatcher.dispatch_due()

            # The owner's notification was skipped, so the first event is the stranger's.
            event = socket.receive_json()
            assert event["data"]["id"] == own.id


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        "[1, 2]",
        json.dumps({"type": "auth"}),
        json.dumps({"type": "auth", "token": 123}),
        json.dumps({"type": "subscribe", "token": "x"}),
        json.dumps({"type": "auth", "token": "invalid"}),
    ],
)
def test_socket_rejects_bad_authentication(api, message):
    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(message)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
    assert closed.value.code == 4401


def test_socket_times_out_when_no_token_arrives(api, monkeypatch):
    monkeypatch.setattr(settings, "ws_auth_timeout_seconds", 0.05)
    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
    assert closed.value.code == 4401


def test_socket_rejects_a_token_for_a_deactivated_account(api, db: Session):
    account = register(db)
    token = auth_service.create_token_for(account, business_of(db, account).id)
    account.is_active = False
    db.commit()

    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
    assert closed.value.code == 4401


def test_socket_closes_when_its_token_expires(api, db: Session, monkeypatch):
    account = register(db)
    token = auth_service.create_token_for(account, business_of(db, account).id)
    real_authenticate = notification_dependencies.authenticate_socket_token

    def expiring_soon(raw_token):
        identity = real_authenticate(raw_token)
        return notification_dependencies.SocketIdentity(
            identity.user_id,
            identity.business_id,
            expires_at=time.time() + 0.1,
        )

    monkeypatch.setattr(
        "app.expense_manager.notification.router.authenticate_socket_token",
        expiring_soon,
    )
    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            assert socket.receive_json()["type"] == "connection.ready"
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
    assert closed.value.code == 4401


def test_socket_limit_per_user(api, db: Session, monkeypatch):
    monkeypatch.setattr(settings, "ws_max_connections_per_user", 1)
    account = register(db)
    token = auth_service.create_token_for(account, business_of(db, account).id)

    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as first:
            first.send_text(json.dumps({"type": "auth", "token": token}))
            first.receive_json()
            with client.websocket_connect(f"{URL}/ws") as second:
                second.send_text(json.dumps({"type": "auth", "token": token}))
                with pytest.raises(WebSocketDisconnect) as closed:
                    second.receive_json()
    assert closed.value.code == 4429


def test_socket_ignores_binary_frames(api, db: Session):
    account = register(db)
    token = auth_service.create_token_for(account, business_of(db, account).id)
    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            socket.receive_json()
            socket.send_bytes(b"\x00")
            socket.send_text("ping")
            assert socket.receive_text() == "pong"


def test_closed_sockets_are_forgotten(api, db: Session):
    account = register(db)
    token = auth_service.create_token_for(account, business_of(db, account).id)
    with TestClient(api) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            socket.receive_json()
            assert account.user_id in ws_manager._connections
        client.portal.call(asyncio.sleep, 0.05)
        assert account.user_id not in ws_manager._connections


def test_events_go_through_redis_when_configured(monkeypatch):
    published = []

    class FakeRedis:
        def publish(self, channel, payload):
            published.append((channel, json.loads(payload)))

    monkeypatch.setattr(settings, "redis_url", "redis://example")
    monkeypatch.setattr(websockets, "_redis_client", lambda: FakeRedis())

    assert websockets.publish_event(7, {"type": "x"}) is True
    assert published == [(websockets.REDIS_CHANNEL, {"user_id": 7, "event": {"type": "x"}})]


def test_redis_outage_retries_the_websocket_delivery(db: Session, monkeypatch):
    class DownRedis:
        def publish(self, channel, payload):
            raise ConnectionError("redis down")

    monkeypatch.setattr(settings, "redis_url", "redis://example")
    monkeypatch.setattr(websockets, "_redis_client", lambda: DownRedis())
    account = register(db)
    notification = publish_committed(db, account.user_id)
    delivery = deliveries(db, notification.id)["websocket"]
    delivery.attempts = 1
    db.commit()

    outbox_dispatcher.deliver(delivery.id)

    delivery = deliveries(db, notification.id)["websocket"]
    assert delivery.status == "pending"
    assert delivery.last_error == "redis down"


def test_read_sync_survives_a_redis_outage(api, db: Session, monkeypatch):
    class DownRedis:
        def publish(self, channel, payload):
            raise ConnectionError("redis down")

    account = register(db)
    notification = publish_committed(db, account.user_id)
    monkeypatch.setattr(settings, "redis_url", "redis://example")
    monkeypatch.setattr(websockets, "_redis_client", lambda: DownRedis())

    response = client_for(api, db, account).post(f"{URL}/read", json={"ids": [notification.id]})

    assert response.status_code == 200
    assert response.json()["updated"] == 1


def test_listener_forwards_events_and_reconnects_after_a_failure(monkeypatch):
    received = []
    connections = []

    class FakePubSub:
        def __init__(self, index):
            self.index = index
            self.reads = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def subscribe(self, channel):
            assert channel == websockets.REDIS_CHANNEL

        async def get_message(self, **_):
            self.reads += 1
            if self.index == 0:
                raise ConnectionError("dropped")
            if self.reads == 1:
                return {"data": json.dumps({"user_id": 3, "event": {"type": "x"}})}
            if self.reads == 2:
                return None
            await asyncio.sleep(3600)

    class FakeClient:
        def __init__(self):
            self.index = len(connections)
            connections.append(self)
            self.closed = False

        def pubsub(self):
            return FakePubSub(self.index)

        async def aclose(self):
            self.closed = True

    async def scenario():
        manager = websockets.ConnectionManager()

        async def record(user_id, event):
            received.append((user_id, event))

        manager.send_to_user = record
        monkeypatch.setattr(websockets.asyncio, "sleep", _fast_sleep)
        task = asyncio.create_task(manager._listen())
        for _ in range(200):
            await _real_sleep(0.001)
            if received:
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _real_sleep = asyncio.sleep

    async def _fast_sleep(delay):
        await _real_sleep(0 if delay < 60 else delay)

    monkeypatch.setattr(settings, "redis_url", "redis://example")
    monkeypatch.setattr(websockets.aioredis, "from_url", lambda *a, **k: FakeClient())
    asyncio.run(scenario())

    assert received == [(3, {"type": "x"})]
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)


def test_failed_socket_is_dropped_and_others_still_receive():
    class Good:
        def __init__(self):
            self.events = []

        async def send_json(self, event):
            self.events.append(event)

    class Dead:
        async def send_json(self, event):
            raise RuntimeError("gone")

    async def scenario():
        manager = websockets.ConnectionManager()
        good, dead = Good(), Dead()
        manager.connect(1, good)
        manager.connect(1, dead)
        await manager.send_to_user(1, {"type": "x"})
        return manager, good, dead

    manager, good, dead = asyncio.run(scenario())
    assert good.events == [{"type": "x"}]
    assert manager._connections[1] == {good}


# ---- Firebase ----


def build_message(**overrides) -> PushMessage:
    values = dict(
        title="Low balance",
        body="Card ending 1234 is down to PKR 0.00",
        data={"notification_id": "abc", "type": "low_balance"},
        category="alerts",
        high_priority=True,
        ttl_seconds=86400,
        badge=3,
        link="https://app.example.com/cards/1",
        icon="https://app.example.com/icon.png",
    )
    values.update(overrides)
    return PushMessage(**values)


def encode(multicast: messaging.MulticastMessage) -> dict:
    """Run the SDK's own validation, the same as a real send does."""
    return MessageEncoder().default(
        EncodedMessage(
            data=multicast.data,
            notification=multicast.notification,
            android=multicast.android,
            webpush=multicast.webpush,
            apns=multicast.apns,
            fid=multicast.fids[0],
        )
    )


def test_fcm_payload_covers_all_three_platforms():
    payload = encode(firebase_provider._build_message([], ["fid"], build_message()))

    assert payload["fid"] == "fid"
    assert payload["notification"]["title"] == "Low balance"
    assert payload["android"]["priority"] == "high"
    assert payload["android"]["ttl"] == "86400s"
    assert payload["android"]["notification"]["channel_id"] == "expense_alerts"
    assert payload["apns"]["headers"]["apns-priority"] == "10"
    assert payload["apns"]["payload"]["aps"]["badge"] == 3
    assert payload["webpush"]["headers"]["Urgency"] == "high"
    assert payload["webpush"]["fcm_options"]["link"] == "https://app.example.com/cards/1"
    assert payload["webpush"]["notification"]["tag"] == "abc"


def test_fcm_payload_is_valid_without_optional_fields():
    payload = encode(
        firebase_provider._build_message(
            [],
            ["fid"],
            build_message(high_priority=False, badge=None, link=None, icon=None),
        )
    )

    assert payload["android"]["priority"] == "normal"
    assert payload["apns"]["headers"]["apns-priority"] == "5"
    assert "fcm_options" not in payload["webpush"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (messaging.UnregisteredError("gone"), PushStatus.invalid_token),
        (messaging.SenderIdMismatchError("other project"), PushStatus.invalid_token),
        (firebase_exceptions.InvalidArgumentError("bad"), PushStatus.rejected),
        (messaging.QuotaExceededError("slow down"), PushStatus.retryable),
        (firebase_exceptions.UnavailableError("later"), PushStatus.retryable),
        (firebase_exceptions.InternalError("oops"), PushStatus.retryable),
    ],
)
def test_fcm_errors_are_classified(error, expected):
    class Response:
        success = False
        exception = error

    assert firebase_provider._to_result("token", Response()).status == expected


def test_large_sends_are_split_into_fcm_batches(monkeypatch):
    sent = []

    class Result:
        success = True
        exception = None

    def fake_send(message, app=None):
        sent.append(len(message.tokens))

        class Batch:
            responses = [Result() for _ in message.tokens]

        return Batch()

    monkeypatch.setattr(firebase_provider.messaging, "send_each_for_multicast", fake_send)
    provider_instance = firebase_provider.FirebasePushProvider.__new__(
        firebase_provider.FirebasePushProvider
    )
    provider_instance._app = None

    results = provider_instance.send(
        [PushTarget(f"token-{i}") for i in range(501)],
        build_message(),
    )

    assert sent == [500, 1]
    assert len(results) == 501
    assert {result.status for result in results} == {PushStatus.delivered}


def test_mixed_targets_use_the_right_fcm_field_and_map_results_back(monkeypatch):
    captured = {}

    class Ok:
        success = True
        exception = None

    class Gone:
        success = False
        exception = messaging.UnregisteredError("gone")

    def fake_send(message, app=None):
        captured["tokens"] = message.tokens
        captured["fids"] = message.fids

        # Firebase replies for tokens first, then FIDs: the second FID is dead.
        class Batch:
            responses = [Ok(), Ok(), Gone()]

        return Batch()

    monkeypatch.setattr(firebase_provider.messaging, "send_each_for_multicast", fake_send)
    provider_instance = firebase_provider.FirebasePushProvider.__new__(
        firebase_provider.FirebasePushProvider
    )
    provider_instance._app = None

    results = provider_instance.send(
        [
            PushTarget("fid-1", is_fid=True),
            PushTarget("legacy-token"),
            PushTarget("fid-2", is_fid=True),
        ],
        build_message(),
    )

    assert captured == {"tokens": ["legacy-token"], "fids": ["fid-1", "fid-2"]}
    assert {result.identifier: result.status for result in results} == {
        "legacy-token": PushStatus.delivered,
        "fid-1": PushStatus.delivered,
        "fid-2": PushStatus.invalid_token,
    }


def test_fid_only_send_does_not_touch_the_deprecated_tokens_field(recwarn):
    multicast = firebase_provider._build_message([], ["fid"], build_message())

    assert multicast.tokens is None
    assert not [item for item in recwarn if "deprecated" in str(item.message)]


def test_registered_token_type_decides_how_a_device_is_targeted(api, db: Session, provider):
    account = register(db)
    client = client_for(api, db, account)
    client.post(
        f"{URL}/devices",
        json={"token": "installation-id-00000000", "platform": "android", "token_type": "fid"},
    )
    client.post(f"{URL}/devices", json={"token": "legacy-token-00000000000", "platform": "web"})
    notification = publish_committed(db, account.user_id)

    claim_and_deliver_push(db, notification.id)

    assert {target.identifier: target.is_fid for target in provider.targets} == {
        "installation-id-00000000": True,
        "legacy-token-00000000000": False,
    }


def test_web_link_needs_https(monkeypatch):
    notification = ExpenseNotification(action_url="/cards/1")

    monkeypatch.setattr(settings, "web_base_url", "https://app.example.com/")
    assert push._web_link(notification) == "https://app.example.com/cards/1"
    monkeypatch.setattr(settings, "web_base_url", "http://localhost:3000")
    assert push._web_link(notification) is None
    monkeypatch.setattr(settings, "web_base_url", None)
    assert push._web_link(notification) is None


# ---- Workers ----


def test_beat_schedule_only_names_registered_tasks():
    registered = celery_module.celery_app.tasks
    for entry in scheduler.BEAT_SCHEDULE.values():
        assert entry["task"] in registered


def test_celery_dispatch_enqueues_with_an_expiry(db: Session, monkeypatch):
    account = register(db)
    publish_committed(db, account.user_id)
    queued = []
    monkeypatch.setattr(
        notification_tasks.deliver_task,
        "apply_async",
        lambda args, expires: queued.append((args, expires)),
    )

    assert notification_tasks.dispatch_outbox_task() == 2
    assert len(queued) == 2
    assert all(expires == settings.claim_visibility_seconds for _, expires in queued)


def test_broker_outage_leaves_work_claimable(db: Session, monkeypatch):
    account = register(db)
    notification = publish_committed(db, account.user_id)

    def broken_enqueue(delivery_id):
        raise ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        outbox_dispatcher.dispatch_due(broken_enqueue)

    for row in deliveries(db, notification.id).values():
        assert row.status == "pending"
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert outbox_dispatcher.dispatch_due() == 2


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("dispatch", "Delivered 2 notifications"),
        ("reminders", "Queued 0 card expiry reminders"),
        ("weekly-summaries", "Queued 0 weekly summaries"),
        ("purge", "Purged:"),
    ],
)
def test_cron_commands_run_once(db: Session, capsys, command, expected):
    account = register(db)
    publish_committed(db, account.user_id)

    scheduler._run_once(command)

    assert expected in capsys.readouterr().out


def test_real_app_startup_runs_inline_delivery_to_a_live_socket(engine, db: Session, monkeypatch):
    # Database setup is stubbed so startup can't reach the configured server.
    noop = lambda *args, **kwargs: None
    for name in (
        "ensure_admin_profile_columns",
        "ensure_default_superadmin",
        "init_builtin_roles",
        "init_control_tables",
    ):
        monkeypatch.setattr(main, name, noop)
    monkeypatch.setattr(main.Base.metadata, "create_all", noop)
    monkeypatch.setattr(main, "get_db", lambda: iter([SimpleNamespace(close=noop)]))
    monkeypatch.setattr(settings, "dispatch_interval_seconds", 0.02)
    monkeypatch.setitem(main.app.dependency_overrides, get_db, lambda: db)

    account = register(db)
    token = auth_service.create_token_for(account, business_of(db, account).id)

    with TestClient(main.app) as client:
        with client.websocket_connect(f"{URL}/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            assert socket.receive_json()["type"] == "connection.ready"

            notification = publish_committed(db, account.user_id)

            event = socket.receive_json()
            assert event["type"] == "notification.created"
            assert event["data"]["id"] == notification.id

    assert ws_manager._loop is None


def test_inline_dispatcher_only_runs_without_redis(monkeypatch):
    async def scenario():
        monkeypatch.setattr(settings, "redis_url", "redis://example")
        assert scheduler.start_inline_dispatcher() is None

        monkeypatch.setattr(settings, "redis_url", None)
        calls = []
        monkeypatch.setattr(outbox_dispatcher, "dispatch_due", lambda: calls.append(1))
        monkeypatch.setattr(settings, "dispatch_interval_seconds", 0.01)
        task = scheduler.start_inline_dispatcher()
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert calls

    asyncio.run(scenario())


# ---- Migration ----


def test_migration_matches_the_models_and_tolerates_existing_tables():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "alembic" / "versions" / (
        "b4c5d6e7f8a9_add_expense_notification_tables.py"
    )
    spec = importlib.util.spec_from_file_location("notification_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://", poolclass=StaticPool)
    prerequisite_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in migration.TABLES
    ]
    Base.metadata.create_all(bind=engine, tables=prerequisite_tables)

    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        original = migration.op
        migration.op = operations
        operations.get_bind = lambda: connection
        try:
            migration.upgrade()
            # Startup's create_all may already have made the tables.
            migration.upgrade()

            context = MigrationContext.configure(connection)
            differences = [
                difference
                for difference in compare_metadata(context, Base.metadata)
                if "expense_notification" in str(difference)
            ]
            assert differences == []

            migration.downgrade()
            assert not set(migration.TABLES) & set(inspect(connection).get_table_names())
        finally:
            migration.op = original
