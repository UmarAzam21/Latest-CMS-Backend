"""add type to expense category options

Revision ID: f3a7c9d1e5b2
Revises: d7e8f9a012b3
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a7c9d1e5b2"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a012b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "expense_category_options" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("expense_category_options")}
    if "type" not in columns:
        op.add_column(
            "expense_category_options",
            sa.Column(
                "type",
                sa.Enum("expense", "income", "debt", name="expense_type_enum"),
                nullable=False,
                server_default="expense",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "expense_category_options" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("expense_category_options")}
        if "type" in columns:
            op.drop_column("expense_category_options", "type")