"""add receipt public id to expenses

Revision ID: f7a8b9c0d1e2
Revises: ebf087bb3c0b
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "ebf087bb3c0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "expense_manager_new_entry",
        sa.Column("receipt_public_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("expense_manager_new_entry", "receipt_public_id")
