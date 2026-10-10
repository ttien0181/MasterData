"""Per-run overview summaries. Source counts are backfilled with one scan of the records table."""
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0026_run_summaries"
down_revision = "0025_pipeline_checkpoints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mdm_resolution_run_summaries",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("source_counts", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_version", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # One pass over the records table for every existing run, instead of one pass per overview request.
    rows = op.get_bind().execute(sa.text(
        "SELECT run_id, source_system, COUNT(*) FROM mdm_resolution_records GROUP BY run_id, source_system")).all()
    by_run: dict[str, dict[str, int]] = {}
    for run_id, source, count in rows:
        by_run.setdefault(run_id, {})[source] = count
    if by_run:
        summaries = sa.table(
            "mdm_resolution_run_summaries",
            sa.column("run_id", sa.String(36)),
            sa.column("source_counts", sa.JSON()),
            sa.column("version", sa.Integer()),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        now = datetime.now(UTC)
        op.bulk_insert(summaries, [{"run_id": run_id, "source_counts": counts, "version": 0, "updated_at": now}
                                   for run_id, counts in by_run.items()])


def downgrade():
    op.drop_table("mdm_resolution_run_summaries")
