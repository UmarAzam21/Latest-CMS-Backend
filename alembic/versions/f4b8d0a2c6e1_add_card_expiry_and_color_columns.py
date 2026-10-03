"""add card expiry and color columns

Revision ID: f4b8d0a2c6e1
Revises: f3a7c9d1e5b2
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4b8d0a2c6e1"
down_revision: Union[str, Sequence[str], None] = "f3a7c9d1e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "expense_accounts" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("expense_accounts")}
    if "expiry_month" not in columns:
        op.add_column(
            "expense_accounts",
            sa.Column("expiry_month", sa.Integer(), nullable=True),
        )
    if "expiry_year" not in columns:
        op.add_column(
            "expense_accounts",
            sa.Column("expiry_year", sa.Integer(), nullable=True),
        )
    if "card_color" not in columns:
        op.add_column(
            "expense_accounts",
            sa.Column(
                "card_color",
                sa.Enum("Primary", "Secondary", "Dark", name="cardcolor"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "expense_accounts" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("expense_accounts")}
    for column_name in ("card_color", "expiry_year", "expiry_month"):
        if column_name in columns:
            op.drop_column("expense_accounts", column_name)