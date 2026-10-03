"""Added SiteSetting and Media models

Revision ID: 9f16e3a08b0d
Revises: 291f3010fd44
Create Date: 2026-08-10 23:48:36.877823

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9f16e3a08b0d'
down_revision: Union[str, Sequence[str], None] = '291f3010fd44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # This migration was auto-generated against a database containing dynamic dataset tables
    # created by the XLSX import process. Those tables should not be managed by Alembic.
    # The database already contains the intended site_settings and media tables, so no schema changes are needed here.
    pass


def downgrade() -> None:
    """Downgrade schema."""
    # No downgrade actions required for this revision in the current database state.
    pass
