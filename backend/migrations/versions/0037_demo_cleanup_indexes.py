"""Bound FK checks when expired demo pairs and members are removed."""
from alembic import op
from sqlalchemy import inspect

revision='0037_demo_cleanup_indexes'
down_revision='0036_demo_retention'
branch_labels=depends_on=None
INDEXES=[('ix_audit_workflow_pair','mdm_resolution_audit_events',['workflow_pair_id']),
         ('ix_workflow_member_record','mdm_workflow_members',['record_id']),
         ('ix_pair_left_record','mdm_resolution_pairs',['left_record_id']),
         ('ix_pair_right_record','mdm_resolution_pairs',['right_record_id']),
         ('ix_split_separated_record','mdm_resolution_split_events',['separated_record_id']),
         ('ix_negative_left_record','mdm_resolution_negative_edges',['left_record_id']),
         ('ix_negative_right_record','mdm_resolution_negative_edges',['right_record_id']),
         ('ix_stage_left_record','mdm_resolution_staged_results',['left_id']),
         ('ix_stage_right_record','mdm_resolution_staged_results',['right_id']),
         ('ix_stage_record','mdm_resolution_staged_results',['record_id'])]


def upgrade():
    connection=op.get_bind()
    for name,table,columns in INDEXES:
        if name not in {item['name'] for item in inspect(connection).get_indexes(table)}:
            if connection.dialect.name=='postgresql':
                with op.get_context().autocommit_block():
                    op.create_index(name,table,columns,postgresql_concurrently=True,if_not_exists=True)
            else:
                op.create_index(name,table,columns)


def downgrade():
    for name,table,_ in reversed(INDEXES): op.drop_index(name,table_name=table)
