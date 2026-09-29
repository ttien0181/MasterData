"""Owner for new MDM imports; legacy stays unclaimed.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mdm_imports") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_mdm_imports_owner_user_id_users", "users", ["owner_user_id"], ["id"])
        batch.create_index("ix_mdm_imports_owner_user_id", ["owner_user_id"])


def downgrade():
    with op.batch_alter_table("mdm_imports") as batch:
        batch.drop_index("ix_mdm_imports_owner_user_id")
        batch.drop_constraint("fk_mdm_imports_owner_user_id_users", type_="foreignkey")
        batch.drop_column("owner_user_id")
