"""0017: R2 object storage — nullable csv_bytes, r2_key on exports and files."""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016_rbac_role_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ResolutionExport: make csv_bytes nullable, add r2_key
    op.alter_column("mdm_resolution_exports", "csv_bytes", existing_type=sa.LargeBinary(),
                    nullable=True)
    op.add_column("mdm_resolution_exports",
                  sa.Column("r2_key", sa.String(512), nullable=True))

    # ResolutionFile: add r2_key for original uploaded CSV
    op.add_column("mdm_resolution_files",
                  sa.Column("r2_key", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("mdm_resolution_files", "r2_key")
    op.drop_column("mdm_resolution_exports", "r2_key")
    op.alter_column("mdm_resolution_exports", "csv_bytes", existing_type=sa.LargeBinary(),
                    nullable=False)
