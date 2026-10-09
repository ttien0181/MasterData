"""0017: R2 object storage — nullable csv_bytes, r2_key on exports and files."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016_rbac_role_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ResolutionExport: make csv_bytes nullable, add r2_key
    with op.batch_alter_table("mdm_resolution_exports") as batch:
        batch.alter_column("csv_bytes", existing_type=sa.LargeBinary(), nullable=True)
        batch.add_column(sa.Column("r2_key", sa.String(512), nullable=True))

    # ResolutionFile: add r2_key for original uploaded CSV
    op.add_column("mdm_resolution_files",
                  sa.Column("r2_key", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("mdm_resolution_files", "r2_key")
    with op.batch_alter_table("mdm_resolution_exports") as batch:
        batch.drop_column("r2_key")
        batch.alter_column("csv_bytes", existing_type=sa.LargeBinary(), nullable=False)
