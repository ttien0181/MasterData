"""Trace automatic acceptance separately from human review history."""
import sqlalchemy as sa
from alembic import op

revision='0031_pair_automation'
down_revision='0030_review_reasons'
branch_labels=depends_on=None


def upgrade():
    columns={c['name'] for c in sa.inspect(op.get_bind()).get_columns('mdm_workflow_pairs')}
    if 'automation' not in columns:
        op.add_column('mdm_workflow_pairs',sa.Column('automation',sa.JSON(),nullable=True))


def downgrade():
    # Retain acceptance provenance on rollback, as with existing review history.
    pass
