"""Add session workflow without modifying engine or historical serving rows."""
import sqlalchemy as sa
from alembic import op

revision = '0028_session_workflow'
down_revision = '0027_llm_policy'
branch_labels = depends_on = None


def upgrade():
    existing = {table: {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table)}
                for table in ('mdm_resolution_runs', 'mdm_resolution_upload_sessions', 'mdm_resolution_tasks')}
    for name in ('workflow_version', 'workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision'):
        if name not in existing['mdm_resolution_runs']:
            op.add_column('mdm_resolution_runs', sa.Column(name, sa.Integer(), nullable=False,
                          server_default='1' if name == 'workflow_version' else '0'))
    for table, column in (
        ('mdm_resolution_runs', sa.Column('workflow_state', sa.String(32), nullable=False, server_default='LEGACY_READ_ONLY')),
        ('mdm_resolution_upload_sessions', sa.Column('workflow_version', sa.Integer(), nullable=False, server_default='1')),
        ('mdm_resolution_tasks', sa.Column('operation_id', sa.String(36), nullable=True)),
    ):
        if column.name not in existing[table]:
            op.add_column(table, column)
    from masterone.db.base import Base
    from masterone.mdm import workflow_models  # noqa: F401
    tables = [table for name, table in Base.metadata.tables.items() if name.startswith('mdm_workflow_')]
    Base.metadata.create_all(op.get_bind(), tables=tables)


def downgrade():
    # Preserve immutable decisions, golden choices and history on rollback.
    pass
