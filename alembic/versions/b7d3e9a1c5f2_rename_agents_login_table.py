"""rename agents_login table to agent_register

Revision ID: b7d3e9a1c5f2
Revises: a9c1e4b7d2f0
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d3e9a1c5f2"
down_revision: Union[str, Sequence[str], None] = "a9c1e4b7d2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "agents_login" in tables and "agent_register" not in tables:
        op.rename_table("agents_login", "agent_register")


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_register" in tables and "agents_login" not in tables:
        op.rename_table("agent_register", "agents_login")