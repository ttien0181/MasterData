"""Add merge preview snapshots and idempotent request receipts.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mdm_resolution_previews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_previews_run_id", "mdm_resolution_previews", ["run_id"])
    op.create_index("ix_mdm_resolution_previews_owner_user_id", "mdm_resolution_previews", ["owner_user_id"])
    op.create_table(
        "mdm_resolution_request_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "owner_user_id", "key", name="uq_mdm_resolution_request_key"),
    )
    op.create_index("ix_mdm_resolution_request_keys_run_id", "mdm_resolution_request_keys", ["run_id"])
    op.create_index("ix_mdm_resolution_request_keys_owner_user_id", "mdm_resolution_request_keys", ["owner_user_id"])


def downgrade():
    op.drop_index("ix_mdm_resolution_request_keys_owner_user_id", table_name="mdm_resolution_request_keys")
    op.drop_index("ix_mdm_resolution_request_keys_run_id", table_name="mdm_resolution_request_keys")
    op.drop_table("mdm_resolution_request_keys")
    op.drop_index("ix_mdm_resolution_previews_owner_user_id", table_name="mdm_resolution_previews")
    op.drop_index("ix_mdm_resolution_previews_run_id", table_name="mdm_resolution_previews")
    op.drop_table("mdm_resolution_previews")
