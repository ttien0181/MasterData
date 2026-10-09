"""Widen users.role to 32 chars and set default to VIEWER (§26.1 RBAC — M7).

Previously String(20) with server_default='STEWARD'. Extend to 32 chars for
SENIOR_STEWARD and POLICY_ADMIN; change default to VIEWER (least-privilege).

Revision ID: 0016_rbac_role_widen
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_rbac_role_widen"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(20),
            type_=sa.String(32),
            existing_nullable=False,
            server_default="VIEWER",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(32),
            type_=sa.String(20),
            existing_nullable=False,
            server_default="STEWARD",
        )
