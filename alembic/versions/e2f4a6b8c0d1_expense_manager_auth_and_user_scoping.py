"""expense manager auth table, income_expense.user_id, per-user card numbers

Revision ID: e2f4a6b8c0d1
Revises: b7d3e9a1c5f2
Create Date: 2026-10-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f4a6b8c0d1"
down_revision: Union[str, Sequence[str], None] = "b7d3e9a1c5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _upgrade_expense_manager_auth(bind) -> None:
    inspector = sa.inspect(bind)
    if "expense_manager_auth" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("expense_manager_auth")}
        if "pin_hash" in columns:
            return
        # An earlier draft of this table (plain integer `pin`, no user link) may
        # have been created by Base.metadata.create_all. Replace it only if empty.
        row_count = bind.execute(sa.text("SELECT COUNT(*) FROM expense_manager_auth")).scalar()
        if row_count:
            raise RuntimeError(
                "expense_manager_auth has rows in the old layout (plain PIN, no user_id); "
                "migrate them manually before running this revision."
            )
        op.drop_table("expense_manager_auth")

    op.create_table(
        "expense_manager_auth",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("business_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("pin_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("phone_number"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_expense_manager_auth_id"), "expense_manager_auth", ["id"], unique=False)


def _upgrade_income_expense_user_id(bind) -> None:
    columns = {column["name"] for column in sa.inspect(bind).get_columns("income_expense")}
    if "user_id" in columns:
        return

    op.add_column("income_expense", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_income_expense_user_id", "income_expense", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_income_expense_user_id"), "income_expense", ["user_id"], unique=False)
    # Backfill owners from the linked card. Entries without a card have no
    # recorded owner and stay NULL (they won't appear in anyone's transactions).
    op.execute(
        "UPDATE income_expense SET user_id = ("
        "SELECT expense_accounts.user_id FROM expense_accounts "
        "WHERE expense_accounts.id = income_expense.account_id"
        ") WHERE account_id IS NOT NULL"
    )


def _upgrade_card_number_uniqueness(bind) -> None:
    inspector = sa.inspect(bind)
    unique_names = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints("expense_accounts")
    }
    unique_names.update({
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("expense_accounts")
        if index.get("unique")
    })

    for name, column_names in unique_names.items():
        if column_names == ["account_number"]:
            op.drop_index(name, table_name="expense_accounts")

    if "uq_expense_account_user_number" not in unique_names:
        op.create_unique_constraint(
            "uq_expense_account_user_number", "expense_accounts", ["user_id", "account_number"]
        )


def upgrade() -> None:
    bind = op.get_bind()
    _upgrade_expense_manager_auth(bind)
    _upgrade_income_expense_user_id(bind)
    _upgrade_card_number_uniqueness(bind)


def downgrade() -> None:
    # Restoring the global unique index fails if two users now share last-4 digits.
    op.drop_constraint("uq_expense_account_user_number", "expense_accounts", type_="unique")
    op.create_unique_constraint("account_number", "expense_accounts", ["account_number"])

    op.drop_index(op.f("ix_income_expense_user_id"), table_name="income_expense")
    op.drop_constraint("fk_income_expense_user_id", "income_expense", type_="foreignkey")
    op.drop_column("income_expense", "user_id")

    op.drop_index(op.f("ix_expense_manager_auth_id"), table_name="expense_manager_auth")
    op.drop_table("expense_manager_auth")
