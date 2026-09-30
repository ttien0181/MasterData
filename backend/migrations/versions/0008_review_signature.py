"""Store privacy-safe review cohort signatures.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mdm_resolution_review_cases") as batch:
        batch.add_column(sa.Column("signature_hash", sa.String(64), nullable=False, server_default=""))
        batch.create_index("ix_mdm_resolution_review_cases_signature", ["run_id", "signature_hash"])


def downgrade():
    with op.batch_alter_table("mdm_resolution_review_cases") as batch:
        batch.drop_index("ix_mdm_resolution_review_cases_signature")
        batch.drop_column("signature_hash")
