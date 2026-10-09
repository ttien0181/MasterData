"""Add durable job and stage registry metadata.

Revision ID: 0005_storage_registry
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_storage_registry"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_table(
        "job_stages",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_uri", sa.Text(), nullable=True),
        sa.Column("output_uri", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("schema", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(length=128), nullable=True),
        sa.Column("code_version", sa.String(length=128), nullable=True),
        sa.Column("input_fingerprint", sa.String(length=128), nullable=True),
        sa.UniqueConstraint("job_id", "stage", name="uq_job_stage"),
    )
    op.create_index("ix_job_stages_job_id", "job_stages", ["job_id"])
    op.create_index("ix_job_stages_status", "job_stages", ["status"])


def downgrade() -> None:
    op.drop_index("ix_job_stages_status", table_name="job_stages")
    op.drop_index("ix_job_stages_job_id", table_name="job_stages")
    op.drop_table("job_stages")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
