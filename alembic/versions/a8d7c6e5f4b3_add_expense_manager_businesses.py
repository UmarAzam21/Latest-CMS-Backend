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


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    names = {item["name"] for item in inspector.get_indexes(table)}
    names |= {item["name"] for item in inspector.get_unique_constraints(table)}
    return name in names


def _has_foreign_key(table: str, name: str) -> bool:
    return name in {item["name"] for item in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def _add_business_id(table: str) -> None:
    """Add the business link, skipping whatever an earlier run already added."""
    add_column = not _has_column(table, "business_id")
    add_foreign_key = not _has_foreign_key(table, f"fk_{table}_business_id")
    add_index = not _has_index(table, f"ix_{table}_business_id")

    with op.batch_alter_table(table) as batch:
        if add_column:
            batch.add_column(sa.Column("business_id", sa.Integer(), nullable=True))
        if add_foreign_key:
            batch.create_foreign_key(
                f"fk_{table}_business_id",
                "expense_manager_businesses",
                ["business_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if add_index:
            batch.create_index(f"ix_{table}_business_id", ["business_id"])


def _replace_user_unique(table: str, old_name: str, new_name: str, column: str) -> None:
    # The old constraint is the only index behind the user_id foreign key, and
    # MariaDB refuses to drop it until another index can take over.
    if not _has_index(table, f"ix_{table}_user_id"):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    drop_old = _has_index(table, old_name)
    add_new = not _has_index(table, new_name)
    with op.batch_alter_table(table) as batch:
        if drop_old:
            batch.drop_constraint(old_name, type_="unique")
        if add_new:
            batch.create_unique_constraint(new_name, ["business_id", column])


def _assign_first_business(table: str) -> None:
    op.get_bind().execute(
        sa.text(
            f"UPDATE {table} SET business_id = ("
            "SELECT MIN(b.id) FROM expense_manager_businesses b "
            "JOIN expense_manager_auth a ON a.id = b.account_id "
            f"WHERE a.user_id = {table}.user_id"
            f") WHERE business_id IS NULL AND EXISTS ("
            "SELECT 1 FROM expense_manager_auth a "
            f"WHERE a.user_id = {table}.user_id"
            ")"
        )
    )


def upgrade() -> None:
    # App startup runs create_all, so the businesses table can exist before this
    # migration does. MariaDB can't roll DDL back either, so every step checks
    # first: a run that stopped halfway can simply be run again.
    if not _has_table("expense_manager_businesses"):
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
    if not _has_index("expense_manager_businesses", "ix_expense_manager_businesses_account_id"):
        op.create_index(
            "ix_expense_manager_businesses_account_id",
            "expense_manager_businesses",
            ["account_id"],
            unique=False,
        )
    if not _has_index("expense_manager_businesses", "ix_expense_manager_businesses_id"):
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
            "SELECT a.id, a.business_name, 'personal', 'PKR', CURRENT_TIMESTAMP "
            "FROM expense_manager_auth a "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM expense_manager_businesses b WHERE b.account_id = a.id"
            ")"
        )
    )

    for table in ("expense_accounts", "expense_category_options", "income_expense"):
        _add_business_id(table)
        _assign_first_business(table)

    bind.execute(
        sa.text(
            "UPDATE income_expense SET business_id = ("
            "SELECT business_id FROM expense_accounts "
            "WHERE expense_accounts.id = income_expense.account_id"
            ") WHERE business_id IS NULL AND account_id IS NOT NULL"
        )
    )

    _replace_user_unique(
        "expense_accounts",
        "uq_expense_account_user_number",
        "uq_expense_account_business_number",
        "account_number",
    )
    _replace_user_unique(
        "expense_category_options",
        "uq_expense_category_user_name",
        "uq_expense_category_business_name",
        "name",
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
