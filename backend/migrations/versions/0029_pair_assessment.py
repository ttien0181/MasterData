"""Versioned pair assessment sidecars; historical baselines stay intact."""
import sqlalchemy as sa
from alembic import op

revision = '0029_pair_assessment'
down_revision = '0028_session_workflow'
branch_labels = depends_on = None


def upgrade():
    from masterone.mdm.workflow_models import WorkflowAssessment
    WorkflowAssessment.__table__.create(op.get_bind(), checkfirst=True)
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('mdm_workflow_pairs')}
    for column in (sa.Column('assessment', sa.JSON(none_as_null=True), nullable=True),
                   sa.Column('assessment_id', sa.String(36), nullable=True),
                   sa.Column('advice', sa.JSON(none_as_null=True), nullable=True)):
        if column.name not in columns:
            op.add_column('mdm_workflow_pairs', column)
    indexes = {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('mdm_workflow_pairs')}
    if 'ix_mdm_workflow_pairs_assessment_id' not in indexes:
        op.create_index('ix_mdm_workflow_pairs_assessment_id', 'mdm_workflow_pairs', ['assessment_id'])


def downgrade():
    # Rollback uses the effective assessment pointer; never delete human history.
    pass
