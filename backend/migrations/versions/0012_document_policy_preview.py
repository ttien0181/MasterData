"""Store comparison policy evidence and run-scoped merge previews.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mdm_resolution_runs") as batch:
        batch.add_column(sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("insufficient_count", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("mdm_resolution_pairs") as batch:
        batch.add_column(sa.Column("primary_group", sa.String(32), nullable=True))
        batch.add_column(sa.Column("subgroup", sa.String(80), nullable=True))
        batch.add_column(sa.Column("signature", sa.String(128), nullable=True))
        batch.add_column(sa.Column("normalization_tags", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("policy_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("policy_version", sa.String(40), nullable=True))
    op.create_table(
        "mdm_resolution_previews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("pair_set_hash", sa.String(64), nullable=False),
        sa.Column("proposal_revisions", sa.JSON(), nullable=False),
        sa.Column("choices", sa.JSON(), nullable=False),
        sa.Column("acknowledge_unreviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("execute_request_id", sa.String(80), nullable=True),
        sa.Column("export_id", sa.String(36), sa.ForeignKey("mdm_resolution_exports.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_previews_run_id", "mdm_resolution_previews", ["run_id"])
    op.create_table(
        "mdm_resolution_merge_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preview_id", sa.String(36), sa.ForeignKey("mdm_resolution_previews.id"), nullable=False),
        sa.Column("export_id", sa.String(36), sa.ForeignKey("mdm_resolution_exports.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("proposal_id", sa.String(64), sa.ForeignKey("mdm_resolution_proposals.id"), nullable=False),
        sa.Column("member_record_ids", sa.JSON(), nullable=False),
        sa.Column("pair_ids", sa.JSON(), nullable=False),
        sa.Column("golden_fields", sa.JSON(), nullable=False),
        sa.Column("guard_results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_merge_events_run_id", "mdm_resolution_merge_events", ["run_id"])


def downgrade():
    op.drop_index("ix_mdm_resolution_merge_events_run_id", table_name="mdm_resolution_merge_events")
    op.drop_table("mdm_resolution_merge_events")
    op.drop_index("ix_mdm_resolution_previews_run_id", table_name="mdm_resolution_previews")
    op.drop_table("mdm_resolution_previews")
    with op.batch_alter_table("mdm_resolution_pairs") as batch:
        for column in ("policy_version", "policy_id", "normalization_tags", "signature", "subgroup", "primary_group"):
            batch.drop_column(column)
    with op.batch_alter_table("mdm_resolution_runs") as batch:
        batch.drop_column("insufficient_count")
        batch.drop_column("candidate_count")
