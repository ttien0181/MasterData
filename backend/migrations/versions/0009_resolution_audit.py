"""Persist resolution review decisions with actor and revision context.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mdm_resolution_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pair_id", sa.String(64), sa.ForeignKey("mdm_resolution_pairs.id"), nullable=True),
        sa.Column("proposal_id", sa.String(64), sa.ForeignKey("mdm_resolution_proposals.id"), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_audit_events_run_id", "mdm_resolution_audit_events", ["run_id"])
    op.create_index("ix_mdm_resolution_audit_run_created", "mdm_resolution_audit_events", ["run_id", "created_at"])


def downgrade():
    op.drop_index("ix_mdm_resolution_audit_run_created", table_name="mdm_resolution_audit_events")
    op.drop_index("ix_mdm_resolution_audit_events_run_id", table_name="mdm_resolution_audit_events")
    op.drop_table("mdm_resolution_audit_events")
