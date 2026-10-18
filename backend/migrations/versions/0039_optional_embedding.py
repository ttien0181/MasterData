"""Pin the optional embedding choice to each upload and run; preserve existing runs."""
import sqlalchemy as sa
from alembic import op

revision = '0039_optional_embedding'
down_revision = '0038_demo_feedback_retention'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table in ('mdm_resolution_upload_sessions', 'mdm_resolution_runs'):
        if 'use_embedding' not in {column['name'] for column in inspector.get_columns(table)}:
            op.add_column(table, sa.Column('use_embedding', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    for table in ('mdm_resolution_runs', 'mdm_resolution_upload_sessions'):
        op.drop_column(table, 'use_embedding')
