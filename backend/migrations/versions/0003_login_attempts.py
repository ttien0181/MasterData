"""Persistent login throttle.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_login_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False))
    op.create_index("ix_auth_attempt_ip_time", "auth_login_attempts", ["ip_address", "created_at_epoch"])
    op.create_index("ix_auth_attempt_email_ip_time", "auth_login_attempts", ["email_normalized", "ip_address", "created_at_epoch"])


def downgrade():
    op.drop_table("auth_login_attempts")
