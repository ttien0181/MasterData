"""Add durable job attempts; retain history on rollback/re-upgrade."""
import sqlalchemy as sa
from alembic import op

revision = "0024_job_ownership"
down_revision = "0023_engine_selection"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("mdm_resolution_tasks")}
    for column in (
        sa.Column("attempt_id", sa.String(36)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        if column.name not in columns:
            op.add_column("mdm_resolution_tasks", column)
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("mdm_resolution_tasks")}
    if "ix_mdm_resolution_tasks_queue" not in indexes:
        op.create_index("ix_mdm_resolution_tasks_queue", "mdm_resolution_tasks", ["status", "created_at", "id"])
    if "mdm_resolution_job_attempts" not in sa.inspect(bind).get_table_names():
        op.create_table("mdm_resolution_job_attempts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("mdm_resolution_tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("worker_id", sa.String(128), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("error_code", sa.String(80)))
        for column in ("task_id", "run_id", "lease_expires_at"):
            op.create_index("ix_mdm_resolution_job_attempts_" + column,
                "mdm_resolution_job_attempts", [column])


def downgrade():
    # Stop all external workers before rollback. Old API embeds a worker and
    # cannot honor the new ownership fence. Keep attempts/checkpoints archived.
    pass
