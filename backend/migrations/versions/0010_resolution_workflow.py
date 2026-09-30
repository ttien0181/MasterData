"""Persist item choices, proposal origins and CSV export snapshots.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mdm_resolution_proposals") as batch:
        batch.add_column(sa.Column("origin", sa.String(16), nullable=False, server_default="AUTO"))
    op.execute("UPDATE mdm_resolution_proposals SET origin = 'REVIEW' WHERE status = 'REVIEW_APPROVED'")
    op.create_table(
        "mdm_resolution_choice_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_kind", sa.String(16), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("member_hash", sa.String(64), nullable=False),
        sa.Column("choices", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "item_kind", "item_id", name="uq_mdm_resolution_choice_item"),
    )
    op.create_index("ix_mdm_resolution_choice_drafts_run_id", "mdm_resolution_choice_drafts", ["run_id"])
    op.create_table(
        "mdm_resolution_exports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("csv_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("merged_group_count", sa.Integer(), nullable=False),
        sa.Column("pending_review_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_exports_run_id", "mdm_resolution_exports", ["run_id"])


def downgrade():
    op.drop_index("ix_mdm_resolution_exports_run_id", table_name="mdm_resolution_exports")
    op.drop_table("mdm_resolution_exports")
    op.drop_index("ix_mdm_resolution_choice_drafts_run_id", table_name="mdm_resolution_choice_drafts")
    op.drop_table("mdm_resolution_choice_drafts")
    with op.batch_alter_table("mdm_resolution_proposals") as batch:
        batch.drop_column("origin")
