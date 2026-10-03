"""add user expense categories

Revision ID: c6d7e8f9a012
Revises: f191672609f0
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c6d7e8f9a012"
down_revision: Union[str, Sequence[str], None] = "f191672609f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    category_column = next(
        column for column in inspector.get_columns("income_expense")
        if column["name"] == "category"
    )
    if getattr(category_column["type"], "enums", None):
        op.alter_column(
            "income_expense",
            "category",
            existing_type=mysql.ENUM(
                "utilities", "food", "transport", "rent", "business", "other",
                name="income_expense_category_enum",
            ),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
    op.execute(
        "UPDATE income_expense SET category = CASE category "
        "WHEN 'utilities' THEN 'Utilities' WHEN 'Utilities' THEN 'Utilities' "
        "WHEN 'food' THEN 'Food' WHEN 'Food' THEN 'Food' "
        "WHEN 'transport' THEN 'Transport' WHEN 'Transport' THEN 'Transport' "
        "WHEN 'rent' THEN 'rent' WHEN 'Rent' THEN 'rent' "
        "WHEN 'business' THEN 'Business' WHEN 'Business' THEN 'Business' "
        "WHEN 'other' THEN 'Other' WHEN 'Other' THEN 'Other' "
        "ELSE category END"
    )
    if "expense_category_options" not in inspector.get_table_names():
        op.create_table(
            "expense_category_options",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("color", sa.String(length=7), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "name", name="uq_expense_category_user_name"),
        )

    category_option_id_index = op.f("ix_expense_category_options_id")
    index_names = {
        index["name"] for index in sa.inspect(bind).get_indexes("expense_category_options")
    }
    if category_option_id_index not in index_names:
        op.create_index(
            category_option_id_index,
            "expense_category_options",
            ["id"],
            unique=False,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE income_expense SET category = 'other' "
        "WHERE category NOT IN ('utilities', 'food', 'transport', 'rent', 'business', 'other')"
    )
    op.execute(
        "UPDATE income_expense SET category = CASE category "
        "WHEN 'Utilities' THEN 'utilities' WHEN 'Food' THEN 'food' "
        "WHEN 'Transport' THEN 'transport' WHEN 'Business' THEN 'business' "
        "WHEN 'Other' THEN 'other' END"
    )
    op.drop_index(op.f("ix_expense_category_options_id"), table_name="expense_category_options")
    op.drop_table("expense_category_options")
    op.alter_column(
        "income_expense",
        "category",
        existing_type=sa.String(length=80),
        type_=mysql.ENUM(
            "utilities", "food", "transport", "rent", "business", "other",
            name="income_expense_category_enum",
        ),
        existing_nullable=False,
    )