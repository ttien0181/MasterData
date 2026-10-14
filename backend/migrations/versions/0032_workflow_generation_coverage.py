"""Add virtual generation receipts; preserve old materialized generations."""
import sqlalchemy as sa
from alembic import op

revision = '0032_generation_coverage'
down_revision = '0031_pair_automation'
branch_labels = depends_on = None


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('mdm_workflow_generations')}
    for column in [sa.Column('coverage', sa.JSON(), nullable=False, server_default='{}'),
                   sa.Column('singleton_confirmation', sa.JSON(), nullable=False, server_default='{}'),
                   sa.Column('storage_version', sa.String(32), nullable=False, server_default='workflow_json_v1')]:
        if column.name not in columns:
            op.add_column('mdm_workflow_generations', column)
    # UUID generation + UUID record needs 75 characters. SQLite does not enforce
    # VARCHAR length; widen both FK endpoints explicitly on PostgreSQL.
    for table, name in [('mdm_workflow_clusters', 'id'), ('mdm_workflow_members', 'cluster_id')]:
        column = next(c for c in sa.inspect(op.get_bind()).get_columns(table) if c['name'] == name)
        if column['type'].length < 128:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(name, existing_type=column['type'], type_=sa.String(128))


def downgrade():
    # Keep audited receipts and identifiers; rollback must not discard decisions.
    pass
