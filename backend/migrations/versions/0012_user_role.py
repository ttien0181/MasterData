"""Add role column to users table (mục 26 — RBAC).

Revision ID: 0012_user_role
Revises: 0011_merge_storage_resolution
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_user_role"
down_revision = "0011_merge_storage_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), nullable=False, server_default="STEWARD"),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
