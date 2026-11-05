"""add expense manager notification tables

Revision ID: b4c5d6e7f8a9
Revises: a8d7c6e5f4b3
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a8d7c6e5f4b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "expense_notification_deliveries",
    "expense_notifications",
    "expense_notification_devices",
    "expense_notification_preferences",
)


def upgrade() -> None:
    # App startup runs create_all, so a server started on this code before the
    # migration ran has created these tables already.
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "expense_notifications" not in existing:
        op.create_table(
            "expense_notifications",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("business_id", sa.Integer(), nullable=True),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("priority", sa.String(length=16), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=True),
            sa.Column("action_url", sa.String(length=512), nullable=True),
            sa.Column("dedupe_key", sa.String(length=191), nullable=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["business_id"], ["expense_manager_businesses.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "dedupe_key", name="uq_expense_notification_dedupe"),
            mysql_charset="utf8mb4",
        )
        op.create_index(
            "ix_expense_notifications_feed",
            "expense_notifications",
            ["user_id", "business_id", "created_at"],
        )
        op.create_index(
            "ix_expense_notifications_unread",
            "expense_notifications",
            ["user_id", "read_at"],
        )
        op.create_index(
            "ix_expense_notifications_created_at",
            "expense_notifications",
            ["created_at"],
        )

    if "expense_notification_deliveries" not in existing:
        op.create_table(
            "expense_notification_deliveries",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                autoincrement=True,
                nullable=False,
            ),
            sa.Column("notification_id", sa.String(length=36), nullable=False),
            sa.Column("channel", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
            sa.Column("pending_device_ids", sa.JSON(), nullable=True),
            sa.Column("last_error", sa.String(length=500), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["notification_id"], ["expense_notifications.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "notification_id", "channel", name="uq_expense_notification_delivery"
            ),
            mysql_charset="utf8mb4",
        )
        op.create_index(
            "ix_expense_notification_deliveries_due",
            "expense_notification_deliveries",
            ["status", "next_attempt_at"],
        )

    if "expense_notification_devices" not in existing:
        op.create_table(
            "expense_notification_devices",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=16), nullable=False),
            sa.Column("token", sa.Text(), nullable=False),
            sa.Column(
                "token_type",
                sa.String(length=16),
                nullable=False,
                server_default="fcm_token",
            ),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
            mysql_charset="utf8mb4",
        )
        op.create_index(
            "ix_expense_notification_devices_user",
            "expense_notification_devices",
            ["user_id", "is_active"],
        )

    if "expense_notification_preferences" not in existing:
        op.create_table(
            "expense_notification_preferences",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("push_enabled", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "category", name="uq_expense_notification_preference"),
            mysql_charset="utf8mb4",
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in TABLES:
        if table in existing:
            op.drop_table(table)
