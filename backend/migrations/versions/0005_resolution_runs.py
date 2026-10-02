"""Add independent MDM resolution runs and source records.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mdm_resolution_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="UPLOADED"),
        sa.Column("phase", sa.String(40), nullable=False, server_default="upload"),
        sa.Column("policy_version", sa.String(40), nullable=False, server_default="person-v1"),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mdm_resolution_runs_owner_user_id", "mdm_resolution_runs", ["owner_user_id"])
    op.create_table(
        "mdm_resolution_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sha256", name="uq_mdm_resolution_file_hash"),
    )
    op.create_index("ix_mdm_resolution_files_run_id", "mdm_resolution_files", ["run_id"])
    op.create_table(
        "mdm_resolution_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("mdm_resolution_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False),
        sa.Column("raw_fields", sa.JSON(), nullable=False),
        sa.Column("normalized_fields", sa.JSON(), nullable=False),
        sa.Column("quality_flags", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "external_id", name="uq_mdm_resolution_record_external"),
    )
    op.create_index("ix_mdm_resolution_records_run_id", "mdm_resolution_records", ["run_id"])
    op.create_index("ix_mdm_resolution_records_file_id", "mdm_resolution_records", ["file_id"])
    op.create_index("ix_mdm_resolution_records_run_source", "mdm_resolution_records", ["run_id", "source_system"])
    op.create_table(
        "mdm_resolution_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="QUEUED"),
        sa.Column("phase", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_tasks_status_created", "mdm_resolution_tasks", ["status", "created_at"])


def downgrade():
    op.drop_index("ix_mdm_resolution_tasks_status_created", table_name="mdm_resolution_tasks")
    op.drop_table("mdm_resolution_tasks")
    op.drop_index("ix_mdm_resolution_records_run_source", table_name="mdm_resolution_records")
    op.drop_index("ix_mdm_resolution_records_file_id", table_name="mdm_resolution_records")
    op.drop_index("ix_mdm_resolution_records_run_id", table_name="mdm_resolution_records")
    op.drop_table("mdm_resolution_records")
    op.drop_index("ix_mdm_resolution_files_run_id", table_name="mdm_resolution_files")
    op.drop_table("mdm_resolution_files")
    op.drop_index("ix_mdm_resolution_runs_owner_user_id", table_name="mdm_resolution_runs")
    op.drop_table("mdm_resolution_runs")
