"""merge branches

Revision ID: 385f0569c55f
Revises: b2c3d4e5f6a7, f12a3e4b7d29
Create Date: 2026-09-02 13:58:34.772485

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '385f0569c55f'
down_revision: Union[str, Sequence[str], None] = ('b2c3d4e5f6a7', 'f12a3e4b7d29')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
