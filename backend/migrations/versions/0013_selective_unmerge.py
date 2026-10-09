"""Version exported CSV snapshots and audit selective unmerge.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mdm_resolution_exports") as batch:
        batch.add_column(sa.Column("parent_export_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_mdm_resolution_exports_parent", "mdm_resolution_exports",
                                 ["parent_export_id"], ["id"])
    op.create_table(
        "mdm_resolution_split_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("before_export_id", sa.String(36), sa.ForeignKey("mdm_resolution_exports.id"), nullable=False),
        sa.Column("after_export_id", sa.String(36), sa.ForeignKey("mdm_resolution_exports.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("proposal_id", sa.String(64), sa.ForeignKey("mdm_resolution_proposals.id"), nullable=False),
        sa.Column("separated_record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id"), nullable=False),
        sa.Column("retained_record_ids", sa.JSON(), nullable=False),
        sa.Column("negative_pair_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "request_id", name="uq_mdm_resolution_split_request"),
    )
    op.create_index("ix_mdm_resolution_split_events_run_id", "mdm_resolution_split_events", ["run_id"])


def downgrade():
    op.drop_index("ix_mdm_resolution_split_events_run_id", table_name="mdm_resolution_split_events")
    op.drop_table("mdm_resolution_split_events")
    with op.batch_alter_table("mdm_resolution_exports") as batch:
        batch.drop_constraint("fk_mdm_resolution_exports_parent", type_="foreignkey")
        batch.drop_column("parent_export_id")
