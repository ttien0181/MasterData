"""Versioned compute checkpoints. Rollback preserves private durable history."""
import sqlalchemy as sa
from alembic import op

revision = "0025_pipeline_checkpoints"
down_revision = "0024_job_ownership"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "mdm_checkpoint_objects" not in existing:
        op.create_table("mdm_checkpoint_objects",
            sa.Column("key", sa.String(512), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("byte_count", sa.BigInteger(), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False))
        op.create_index("ix_mdm_checkpoint_objects_run_id", "mdm_checkpoint_objects", ["run_id"])
    if "mdm_checkpoints" not in existing:
        op.create_table("mdm_checkpoints",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attempt_id", sa.String(128), nullable=False),
            sa.Column("stage", sa.String(32), nullable=False),
            sa.Column("scope", sa.String(128), nullable=False),
            sa.Column("pins_sha256", sa.String(64), nullable=False),
            sa.Column("input_sha256", sa.String(64), nullable=False),
            sa.Column("output_sha256", sa.String(64), nullable=False),
            sa.Column("manifest_sha256", sa.String(64), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_mdm_checkpoint_lookup", "mdm_checkpoints",
            ["run_id", "stage", "scope", "pins_sha256", "input_sha256", "state"])
    if "mdm_checkpoint_dependencies" not in existing:
        op.create_table("mdm_checkpoint_dependencies",
            sa.Column("child_id", sa.String(64), sa.ForeignKey("mdm_checkpoints.id"), primary_key=True),
            sa.Column("parent_id", sa.String(64), sa.ForeignKey("mdm_checkpoints.id"), primary_key=True))


def downgrade():
    # Stop workers before rollback. Keep objects/checkpoints for re-upgrade;
    # older code has resume OFF and cannot accidentally consume this version.
    pass
