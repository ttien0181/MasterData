"""Merge storage and resolution migration branches.

Revision ID: 0011_merge_storage_resolution
Revises: 0006_storage_job_owner, 0010
"""

from alembic import op


revision = "0011_merge_storage_resolution"
down_revision = ("0006_storage_job_owner", "0010")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
