"""Update servicetype enum with new values

Revision ID: a1b2c3d4e5f6
Revises: f12a3e4b7d29
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '9f16e3a08b0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Old enum values
old_values = ('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other')

# New enum values
new_values = (
    'business_ntn',
    'simple_ntn_registration',
    'business_registration',
    'company_registration',
    'filer_registration',
    'gst_registration',
    'tax_return_filing',
    'fbr_notices',
    'wealth_statement',
    'dts_registration',
    'imp_exp_license_psw',
    'trade_mark_registration',
    'pec_registration',
    'chamber_membership',
    'pseb',
    'dnfbp',
    'other',
)


def upgrade() -> None:
    """Upgrade schema."""
    # Update old values to 'other' (which exists in both old and new enums)
    op.execute("UPDATE messages SET services = NULL WHERE services NOT IN ('other')")
    op.execute("UPDATE leads SET service_type = 'other' WHERE service_type IN ('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing')")

    service_type_enum = sa.Enum(*new_values, name='servicetype')
    op.alter_column('messages', 'services', type_=service_type_enum,
                    existing_nullable=True)
    op.alter_column('leads', 'service_type', type_=service_type_enum,
                    existing_nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    service_type_enum = sa.Enum(*old_values, name='servicetype')
    op.alter_column('messages', 'services', type_=service_type_enum,
                    existing_nullable=True)
    op.alter_column('leads', 'service_type', type_=service_type_enum,
                    existing_nullable=True)
