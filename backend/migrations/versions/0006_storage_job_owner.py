"""Make storage jobs owner-scoped.

Revision ID: 0006_storage_job_owner
Revises: 0005_storage_registry
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_storage_job_owner"
down_revision = "0005_storage_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_jobs_owner_user_id_users", "users", ["owner_user_id"], ["id"])
        batch.create_index("ix_jobs_owner_user_id", ["owner_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_owner_user_id")
        batch.drop_constraint("fk_jobs_owner_user_id_users", type_="foreignkey")
        batch.drop_column("owner_user_id")
