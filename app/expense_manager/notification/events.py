"""Catalog of every notification the expense manager can send.

Adding a notification means adding a `NotificationType` and its `NotificationSpec`;
nothing else in the engine needs to change.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class NotificationCategory(str, Enum):
    transactions = "transactions"
    alerts = "alerts"
    reminders = "reminders"
    security = "security"
    system = "system"


class NotificationPriority(str, Enum):
    normal = "normal"
    high = "high"


class NotificationType(str, Enum):
    expense_created = "expense_created"
    income_created = "income_created"
    debt_created = "debt_created"
    transfer_completed = "transfer_completed"
    card_added = "card_added"
    low_balance = "low_balance"
    card_expiring = "card_expiring"
    weekly_summary = "weekly_summary"
    pin_changed = "pin_changed"
    account_locked = "account_locked"
    test = "test"


@dataclass(frozen=True)
class CategoryPolicy:
    label: str
    push_default: bool
    # Locked categories can't be turned off by the user.
    locked: bool = False


# Transactions are usually entered by the user themselves, so they sync to open
# dashboards and the inbox, but only reach the lock screen if the user opts in.
CATEGORY_POLICIES = {
    NotificationCategory.transactions: CategoryPolicy("Transactions", push_default=False),
    NotificationCategory.alerts: CategoryPolicy("Balance alerts", push_default=True),
    NotificationCategory.reminders: CategoryPolicy("Reminders and summaries", push_default=True),
    NotificationCategory.security: CategoryPolicy("Security", push_default=True, locked=True),
    NotificationCategory.system: CategoryPolicy("System", push_default=True),
}


@dataclass(frozen=True)
class NotificationSpec:
    category: NotificationCategory
    title: str
    body: str
    action_url: Optional[str] = None
    priority: NotificationPriority = NotificationPriority.normal


# Templates are rendered with `str.format` from the `context` given to `publish`.
# Decimal values are formatted with the business's currency first.
NOTIFICATION_SPECS = {
    NotificationType.expense_created: NotificationSpec(
        NotificationCategory.transactions,
        "Expense added",
        "{amount} for {subject}",
        "/transactions/{entry_id}",
    ),
    NotificationType.income_created: NotificationSpec(
        NotificationCategory.transactions,
        "Income added",
        "{amount} from {subject}",
        "/transactions/{entry_id}",
    ),
    NotificationType.debt_created: NotificationSpec(
        NotificationCategory.transactions,
        "Debt added",
        "{amount} for {subject}",
        "/transactions/{entry_id}",
    ),
    NotificationType.transfer_completed: NotificationSpec(
        NotificationCategory.transactions,
        "Transfer completed",
        "{amount} moved from card ending {source_last_4} to card ending {target_last_4}",
        "/cards",
    ),
    NotificationType.card_added: NotificationSpec(
        NotificationCategory.transactions,
        "Card added",
        "Card ending {last_4} is ready to use",
        "/cards/{card_id}",
    ),
    NotificationType.low_balance: NotificationSpec(
        NotificationCategory.alerts,
        "Low balance",
        "Card ending {last_4} is down to {balance}",
        "/cards/{card_id}",
        NotificationPriority.high,
    ),
    NotificationType.card_expiring: NotificationSpec(
        NotificationCategory.reminders,
        "Card expiring soon",
        "Card ending {last_4} expires {expiry}",
        "/cards/{card_id}",
    ),
    NotificationType.weekly_summary: NotificationSpec(
        NotificationCategory.reminders,
        "Your weekly summary",
        "Last 7 days: {income} income, {expenses} expenses",
        "/transactions",
    ),
    NotificationType.pin_changed: NotificationSpec(
        NotificationCategory.security,
        "PIN changed",
        "Your PIN was just changed. If this wasn't you, contact support now.",
        priority=NotificationPriority.high,
    ),
    NotificationType.account_locked: NotificationSpec(
        NotificationCategory.security,
        "Account temporarily locked",
        "Too many incorrect PIN attempts. Sign-in is locked for {minutes} minutes.",
        priority=NotificationPriority.high,
    ),
    NotificationType.test: NotificationSpec(
        NotificationCategory.system,
        "Test notification",
        "Notifications are working on this device.",
    ),
}
