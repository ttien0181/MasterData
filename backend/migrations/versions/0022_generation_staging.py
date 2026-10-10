"""Add private generation staging without rewriting serving or human history.

Downgrade retains additive columns/tables as an archive. Old runtime ignores
them; re-upgrade reuses checkpoints. Destructive cleanup needs a separate,
explicit retention/backup operation, never an automatic schema rollback.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_generation_staging"
down_revision = "0021_input_contract"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "mdm_resolution_generations" not in tables:
        op.create_table("mdm_resolution_generations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("identity_sha256", sa.String(64), nullable=False),
            sa.Column("snapshot_sha256", sa.String(64), nullable=False),
            sa.Column("review_sha256", sa.String(64), nullable=False),
            sa.Column("config_sha256", sa.String(64), nullable=False),
            sa.Column("base_generation_id", sa.String(36)),
            sa.Column("artifact_backend", sa.String(8), nullable=False),
            sa.Column("state", sa.String(16), nullable=False),
            sa.Column("record_count", sa.BigInteger(), nullable=False),
            sa.Column("expected_counts", sa.JSON()),
            sa.Column("manifest_sha256", sa.String(64)),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("promoted_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("run_id", "identity_sha256", name="uq_mdm_generation_identity"))
        op.create_index("ix_mdm_resolution_generations_run_id", "mdm_resolution_generations", ["run_id"])
    if "mdm_resolution_generation_attempts" not in tables:
        op.create_table("mdm_resolution_generation_attempts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("generation_id", sa.String(36), sa.ForeignKey("mdm_resolution_generations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("error_code", sa.String(80)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True)))
        op.create_index("ix_mdm_resolution_generation_attempts_generation_id", "mdm_resolution_generation_attempts", ["generation_id"])
    if "mdm_resolution_publications" not in tables:
        op.create_table("mdm_resolution_publications",
            sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("active_generation_id", sa.String(36), sa.ForeignKey("mdm_resolution_generations.id", ondelete="SET NULL")),
            sa.Column("pipeline_attempt_id", sa.String(36), sa.ForeignKey("mdm_resolution_generation_attempts.id", ondelete="SET NULL")))
    if "mdm_resolution_staged_results" not in tables:
        op.create_table("mdm_resolution_staged_results",
            sa.Column("generation_id", sa.String(36), sa.ForeignKey("mdm_resolution_generations.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("kind", sa.String(16), primary_key=True),
            sa.Column("row_key", sa.String(64), primary_key=True),
            sa.Column("partition_no", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("left_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id")),
            sa.Column("right_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id")),
            sa.Column("record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id")),
            sa.Column("proposal_id", sa.String(64)),
            sa.CheckConstraint("kind IN ('pair','proposal','membership','golden','review')", name="ck_mdm_stage_kind"),
            sa.CheckConstraint("kind != 'pair' OR left_id < right_id", name="ck_mdm_stage_pair_order"),
            sa.UniqueConstraint("generation_id", "kind", "left_id", "right_id", name="uq_mdm_stage_pair"),
            sa.UniqueConstraint("generation_id", "kind", "record_id", name="uq_mdm_stage_member"))
        op.create_index("ix_mdm_stage_partition", "mdm_resolution_staged_results", ["generation_id", "kind", "partition_no"])
    if "mdm_resolution_partition_receipts" not in tables:
        op.create_table("mdm_resolution_partition_receipts",
            sa.Column("generation_id", sa.String(36), sa.ForeignKey("mdm_resolution_generations.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("kind", sa.String(16), primary_key=True),
            sa.Column("partition_no", sa.Integer(), primary_key=True),
            sa.Column("attempt_id", sa.String(36), sa.ForeignKey("mdm_resolution_generation_attempts.id"), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("byte_count", sa.BigInteger(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("object_key", sa.String(512)),
            sa.Column("artifact_bytes", sa.LargeBinary()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("row_count >= 0 AND byte_count > 0", name="ck_mdm_receipt_counts"))


def downgrade():
    # Preserve receipts, active publication identity and every saved result.
    # Roll back runtime/feature flag first; old code safely ignores these tables.
    pass
