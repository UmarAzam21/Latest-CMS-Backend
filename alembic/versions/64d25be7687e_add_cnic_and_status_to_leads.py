from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '64d25be7687e'
down_revision: Union[str, Sequence[str], None] = '33d730446254'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('leads', 'email',
               existing_type=sa.VARCHAR(length=320),
               nullable=True)
    op.alter_column('leads', 'service_type',
               existing_type=postgresql.ENUM('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other', name='servicetype'),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('leads', 'service_type',
               existing_type=postgresql.ENUM('web_development', 'seo', 'graphic_design', 'digital_marketing', 'app_development', 'content_writing', 'other', name='servicetype'),
               nullable=False)
    op.alter_column('leads', 'email',
               existing_type=sa.VARCHAR(length=320),
               nullable=False)