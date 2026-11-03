"""add expense manager businesses and per-business expense data

Revision ID: a8d7c6e5f4b3
Revises: e2f4a6b8c0d1
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8d7c6e5f4b3"
down_revision: Union[str, Sequence[str], None] = "e2f4a6b8c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expense_manager_businesses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("business_type", sa.String(length=50), nullable=False, server_default="personal"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="PKR"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["account_id"], ["expense_manager_auth.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "name", name="uq_expense_business_account_name"),
    )
    op.create_index(
        "ix_expense_manager_businesses_account_id",
        "expense_manager_businesses",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_expense_manager_businesses_id",
        "expense_manager_businesses",
        ["id"],
        unique=False,
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO expense_manager_businesses "
            "(account_id, name, business_type, currency, created_at) "
            "SELECT id, business_name, 'personal', 'PKR', CURRENT_TIMESTAMP "
            "FROM expense_manager_auth"
        )
    )

    with op.batch_alter_table("expense_accounts") as batch:
        batch.add_column(sa.Column("business_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_expense_accounts_business_id",
            "expense_manager_businesses",
            ["business_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint("uq_expense_account_user_number", type_="unique")
        batch.create_unique_constraint(
            "uq_expense_account_business_number",
            ["business_id", "account_number"],
        )
        batch.create_index("ix_expense_accounts_business_id", ["business_id"])

    with op.batch_alter_table("expense_category_options") as batch:
        batch.add_column(sa.Column("business_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_expense_category_options_business_id",
            "expense_manager_businesses",
            ["business_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint("uq_expense_category_user_name", type_="unique")
        batch.create_unique_constraint(
            "uq_expense_category_business_name",
            ["business_id", "name"],
        )
        batch.create_index("ix_expense_category_options_business_id", ["business_id"])

    with op.batch_alter_table("income_expense") as batch:
        batch.add_column(sa.Column("business_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_income_expense_business_id",
            "expense_manager_businesses",
            ["business_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_income_expense_business_id", ["business_id"])

    bind.execute(
        sa.text(
            "UPDATE expense_accounts SET business_id = ("
            "SELECT b.id FROM expense_manager_businesses b "
            "JOIN expense_manager_auth a ON a.id = b.account_id "
            "WHERE a.user_id = expense_accounts.user_id"
            ") WHERE EXISTS ("
            "SELECT 1 FROM expense_manager_auth a "
            "WHERE a.user_id = expense_accounts.user_id"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE expense_category_options SET business_id = ("
            "SELECT b.id FROM expense_manager_businesses b "
            "JOIN expense_manager_auth a ON a.id = b.account_id "
            "WHERE a.user_id = expense_category_options.user_id"
            ") WHERE EXISTS ("
            "SELECT 1 FROM expense_manager_auth a "
            "WHERE a.user_id = expense_category_options.user_id"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE income_expense SET business_id = ("
            "SELECT b.id FROM expense_manager_businesses b "
            "JOIN expense_manager_auth a ON a.id = b.account_id "
            "WHERE a.user_id = income_expense.user_id"
            ") WHERE EXISTS ("
            "SELECT 1 FROM expense_manager_auth a "
            "WHERE a.user_id = income_expense.user_id"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE income_expense SET business_id = ("
            "SELECT business_id FROM expense_accounts "
            "WHERE expense_accounts.id = income_expense.account_id"
            ") WHERE business_id IS NULL AND account_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("income_expense") as batch:
        batch.drop_index("ix_income_expense_business_id")
        batch.drop_constraint("fk_income_expense_business_id", type_="foreignkey")
        batch.drop_column("business_id")

    with op.batch_alter_table("expense_category_options") as batch:
        batch.drop_index("ix_expense_category_options_business_id")
        batch.drop_constraint("uq_expense_category_business_name", type_="unique")
        batch.drop_constraint("fk_expense_category_options_business_id", type_="foreignkey")
        batch.drop_column("business_id")
        batch.create_unique_constraint(
            "uq_expense_category_user_name",
            ["user_id", "name"],
        )

    with op.batch_alter_table("expense_accounts") as batch:
        batch.drop_index("ix_expense_accounts_business_id")
        batch.drop_constraint("uq_expense_account_business_number", type_="unique")
        batch.drop_constraint("fk_expense_accounts_business_id", type_="foreignkey")
        batch.drop_column("business_id")
        batch.create_unique_constraint(
            "uq_expense_account_user_number",
            ["user_id", "account_number"],
        )

    op.drop_index("ix_expense_manager_businesses_id", table_name="expense_manager_businesses")
    op.drop_index("ix_expense_manager_businesses_account_id", table_name="expense_manager_businesses")
    op.drop_table("expense_manager_businesses")
