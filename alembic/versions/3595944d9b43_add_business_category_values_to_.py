"""add business category values to servicetype enum

Revision ID: 3595944d9b43
Revises: e9016069c7e1
Create Date: 2026-09-02 15:04:40.123591

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3595944d9b43'
down_revision: Union[str, Sequence[str], None] = 'e9016069c7e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_SERVICE_VALUES = (
    'Business NTN',
    'Simple NTN Registration',
    'Business Registration',
    'Company Registration',
    'Filer Registration',
    'GST Registration',
    'Tax Return Filing',
    'FBR Notices',
    'Wealth Statement',
    'DTS Registration',
    'Imp & Exp License (PSW)',
    'Trade Mark Registration',
    'PEC Registration',
    'Chamber Membership',
    'PSEB',
    'DNFBP',
    'Other',
)


def _legacy_service_value_map(value: str | None) -> str | None:
    if value is None:
        return None
    mapping = {
        'business_ntn': 'Business NTN',
        'simple_ntn_registration': 'Simple NTN Registration',
        'business_registration': 'Business Registration',
        'company_registration': 'Company Registration',
        'filer_registration': 'Filer Registration',
        'gst_registration': 'GST Registration',
        'tax_return_filing': 'Tax Return Filing',
        'fbr_notices': 'FBR Notices',
        'wealth_statement': 'Wealth Statement',
        'dts_registration': 'DTS Registration',
        'imp_exp_license_psw': 'Imp & Exp License (PSW)',
        'trade_mark_registration': 'Trade Mark Registration',
        'pec_registration': 'PEC Registration',
        'chamber_membership': 'Chamber Membership',
        'pseb': 'PSEB',
        'dnfbp': 'DNFBP',
        'other': 'Other',
        'web_development': 'Other',
        'seo': 'Other',
        'graphic_design': 'Other',
        'digital_marketing': 'Other',
        'app_development': 'Other',
        'content_writing': 'Other',
    }
    return mapping.get(value, value if value in NEW_SERVICE_VALUES else 'Other')


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # If the enum is already in sync, leave it alone.
    current_labels = bind.execute(sa.text(
        "SELECT array_agg() "
        "FROM pg_enum e JOIN pg_type t ON e.enumtenumlabel ORDER BY enumsortorderypid = t.oid "
        "WHERE t.typname = 'servicetype'"
    )).scalar()
    current_labels = current_labels or []
    if set(current_labels) == set(NEW_SERVICE_VALUES):
        return

    op.execute("ALTER TABLE messages ALTER COLUMN services TYPE TEXT")
    op.execute("ALTER TABLE leads ALTER COLUMN service_type TYPE TEXT")

    # Handle older snake_case values from earlier migrations by mapping them to the new labels.
    op.execute(
        "UPDATE leads SET service_type = CASE "
        "WHEN service_type IS NULL THEN NULL "
        "ELSE 'Other' END "
        "WHERE service_type IS NOT NULL AND service_type NOT IN ('" + "', '".join(NEW_SERVICE_VALUES) + "')"
    )
    op.execute(
        "UPDATE messages SET services = CASE "
        "WHEN services IS NULL THEN NULL "
        "ELSE 'Other' END "
        "WHERE services IS NOT NULL AND services NOT IN ('" + "', '".join(NEW_SERVICE_VALUES) + "')"
    )

    op.execute("ALTER TYPE servicetype RENAME TO servicetype_old")
    op.execute(
        "CREATE TYPE servicetype AS ENUM (" + ", ".join(f"'{v}'" for v in NEW_SERVICE_VALUES) + ")"
    )
    op.execute("ALTER TABLE leads ALTER COLUMN service_type TYPE servicetype USING service_type::servicetype")
    op.execute("ALTER TABLE messages ALTER COLUMN services TYPE servicetype USING services::servicetype")
    op.execute("DROP TYPE servicetype_old")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    current_labels = bind.execute(sa.text(
        "SELECT array_agg(enumlabel ORDER BY enumsortorder) "
        "FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid "
        "WHERE t.typname = 'servicetype'"
    )).scalar()
    current_labels = current_labels or []
    if set(current_labels) == {'web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other'}:
        return

    op.execute("ALTER TABLE messages ALTER COLUMN services TYPE TEXT")
    op.execute("ALTER TABLE leads ALTER COLUMN service_type TYPE TEXT")
    op.execute("ALTER TYPE servicetype RENAME TO servicetype_new")
    op.execute("CREATE TYPE servicetype AS ENUM ('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other')")
    op.execute("UPDATE leads SET service_type = 'other' WHERE service_type IS NULL OR service_type NOT IN ('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other')")
    op.execute("UPDATE messages SET services = 'other' WHERE services IS NULL OR services NOT IN ('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other')")
    op.execute("ALTER TABLE leads ALTER COLUMN service_type TYPE servicetype USING service_type::servicetype")
    op.execute("ALTER TABLE messages ALTER COLUMN services TYPE servicetype USING services::servicetype")
    op.execute("DROP TYPE servicetype_new")
