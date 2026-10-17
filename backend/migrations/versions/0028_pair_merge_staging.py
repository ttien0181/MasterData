"""Mark review pairs selected for deferred merge preview."""

import sqlalchemy as sa
from alembic import op

revision = "0028_pair_merge_staging"
down_revision = "0027_llm_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mdm_resolution_pairs", sa.Column(
        "staged_for_merge", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_mdm_resolution_pairs_run_staged", "mdm_resolution_pairs",
                    ["run_id", "staged_for_merge"])
    op.add_column("mdm_resolution_previews", sa.Column("readiness_snapshot", sa.JSON(), nullable=True))
    op.add_column("mdm_resolution_previews", sa.Column("staged_plan", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("mdm_resolution_previews", "staged_plan")
    op.drop_column("mdm_resolution_previews", "readiness_snapshot")
    op.drop_index("ix_mdm_resolution_pairs_run_staged", table_name="mdm_resolution_pairs")
    op.drop_column("mdm_resolution_pairs", "staged_for_merge")
