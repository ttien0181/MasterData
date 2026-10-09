"""Pin engine profile per run without changing existing run/serving columns."""
import sqlalchemy as sa
from alembic import op

revision = "0023_engine_selection"
down_revision = "0022_generation_staging"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("mdm_resolution_engine_selections"):
        op.create_table("mdm_resolution_engine_selections",
            sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("engine_version", sa.String(32), nullable=False),
            sa.Column("configuration", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    # Retain pinned selection: rollback must not reinterpret saved reviewer state.
    pass
