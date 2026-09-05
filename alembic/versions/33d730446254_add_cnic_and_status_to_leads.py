"""add cnic and status to leads

Revision ID: 33d730446254
Revises: 385f0569c55f
Create Date: 2026-09-02 14:08:13.760189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '33d730446254'
down_revision: Union[str, Sequence[str], None] = '385f0569c55f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('leads', sa.Column('cnic', sa.String(), nullable=True))
    op.add_column('leads', sa.Column('status', sa.String(), nullable=True))
    op.add_column('support_customers', sa.Column('phone', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('support_customers', 'phone')
    op.drop_column('leads', 'status')
    op.drop_column('leads', 'cnic')