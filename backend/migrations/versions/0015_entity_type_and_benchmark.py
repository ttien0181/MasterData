"""Add entity_type, benchmark_entity_id, extra_fields to records; entity_type to proposals.

Revision ID: 0015
Revises: 0014_merge_heads
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mdm_resolution_records",
        sa.Column("entity_type", sa.String(16), nullable=False, server_default="UNKNOWN"))
    op.add_column("mdm_resolution_records",
        sa.Column("benchmark_entity_id", sa.String(200), nullable=True))
    op.add_column("mdm_resolution_records",
        sa.Column("extra_fields", sa.JSON(), nullable=False, server_default="{}"))
    op.create_index(
        "ix_mdm_resolution_records_run_entity_type",
        "mdm_resolution_records",
        ["run_id", "entity_type"],
    )

    op.add_column("mdm_resolution_proposals",
        sa.Column("entity_type", sa.String(16), nullable=False, server_default="UNKNOWN"))


def downgrade() -> None:
    op.drop_column("mdm_resolution_proposals", "entity_type")
    op.drop_index("ix_mdm_resolution_records_run_entity_type", table_name="mdm_resolution_records")
    op.drop_column("mdm_resolution_records", "extra_fields")
    op.drop_column("mdm_resolution_records", "benchmark_entity_id")
    op.drop_column("mdm_resolution_records", "entity_type")
