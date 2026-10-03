"""Added SiteSetting and Media models

Revision ID: 291f3010fd44
Revises: ba247043dfa8
Create Date: 2026-08-10 23:15:13.794874

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic

revision: str = '291f3010fd44'
down_revision: Union[str, Sequence[str], None] = 'ba247043dfa8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    service_type_enum = sa.Enum(
        'web_development',
        'seo',
        'graphic_design',
        'digital_marketing',
        'app_development',
        'content_writing',
        'other',
        name='servicetype',
    )

    op.create_table(
        'leads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column(
            'service_type',
            service_type_enum,
            nullable=False
        ),
        sa.Column('city', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index(
        op.f('ix_leads_id'),
        'leads',
        ['id'],
        unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(
        op.f('ix_leads_id'),
        table_name='leads'
    )

    op.drop_table('leads')