"""add username and role to agents

Revision ID: a9c1e4b7d2f0
Revises: f4b8d0a2c6e1
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c1e4b7d2f0"
down_revision: Union[str, Sequence[str], None] = "f4b8d0a2c6e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agents_login" not in inspector.get_table_names():
        op.create_table(
            "agents_login",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=20), server_default="agent", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=True),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("username", name="uq_agents_login_username"),
            sa.UniqueConstraint("email", name="uq_agents_login_email"),
        )
        op.create_index("ix_agents_login_id", "agents_login", ["id"], unique=False)
        return

    columns = {column["name"] for column in inspector.get_columns("agents_login")}
    if "username" not in columns:
        op.add_column("agents_login", sa.Column("username", sa.String(length=255), nullable=True))
        connection = op.get_bind()
        agent_ids = connection.execute(sa.text("SELECT id FROM agents_login ORDER BY id")).scalars()
        for agent_id in agent_ids:
            connection.execute(
                sa.text("UPDATE agents_login SET username = :username WHERE id = :id"),
                {"username": f"agent_{agent_id}", "id": agent_id},
            )
        op.alter_column("agents_login", "username", nullable=False)
        op.create_unique_constraint("uq_agents_login_username", "agents_login", ["username"])
    if "role" not in columns:
        op.add_column(
            "agents_login",
            sa.Column("role", sa.String(length=20), server_default="agent", nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agents_login" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agents_login")}
    if "username" in columns:
        op.drop_constraint("uq_agents_login_username", "agents_login", type_="unique")
        op.drop_column("agents_login", "username")
    if "role" in columns:
        op.drop_column("agents_login", "role")