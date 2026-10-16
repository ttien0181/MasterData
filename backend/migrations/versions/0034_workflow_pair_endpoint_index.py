"""Index bounded component review by run and original pair endpoints.

Group review reads the immutable baseline endpoints twice per approval. Without
this index PostgreSQL repeatedly detoasts/scans every pair in a large run.
"""
import sqlalchemy as sa
from alembic import op

revision = '0034_pair_endpoint_index'
down_revision = '0033_matching_foundation'
branch_labels = depends_on = None

INDEX = 'ix_m1_pair_run_endpoints'


def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    baseline = sa.column('baseline', sa.JSON)
    # Keep normal review writes available while indexing existing large runs.
    with op.get_context().autocommit_block():
        op.create_index(INDEX, 'mdm_workflow_pairs', [sa.column('run_id'),
            baseline['left_id'].as_string(), baseline['right_id'].as_string()],
            postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            op.drop_index(INDEX, table_name='mdm_workflow_pairs', postgresql_concurrently=True, if_exists=True)
