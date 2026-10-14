"""Preserve the reason for an explicit soft-conflict review."""
import sqlalchemy as sa
from alembic import op

revision = '0030_review_reasons'
down_revision = '0029_pair_assessment'
branch_labels = depends_on = None


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('mdm_workflow_pairs')}
    if 'review_reason' not in columns:
        op.add_column('mdm_workflow_pairs', sa.Column('review_reason', sa.String(500), nullable=True))


def downgrade():
    # Retain human review history during rollback.
    pass
