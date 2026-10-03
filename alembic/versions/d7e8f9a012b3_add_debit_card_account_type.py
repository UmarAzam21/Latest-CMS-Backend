"""add debit card account type

Revision ID: d7e8f9a012b3
Revises: c6d7e8f9a012
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op


revision: str = "d7e8f9a012b3"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE expense_accounts MODIFY account_type "
        "ENUM('personal', 'business', 'debit') NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE expense_accounts SET account_type = 'personal' "
        "WHERE account_type = 'debit'"
    )
    op.execute(
        "ALTER TABLE expense_accounts MODIFY account_type "
        "ENUM('personal', 'business') NOT NULL"
    )