"""Existing MDM schema baseline.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mdm_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False))
    op.create_table("mdm_source_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_id", sa.String(36), sa.ForeignKey("mdm_imports.id"), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("source_record_id", sa.String(200), nullable=False),
        sa.Column("raw_fields", sa.JSON(), nullable=False),
        sa.Column("normalized_fields", sa.JSON(), nullable=False),
        sa.UniqueConstraint("import_id", "source", "source_record_id"))
    op.create_index("ix_mdm_source_records_import_id", "mdm_source_records", ["import_id"])
    op.create_table("mdm_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_id", sa.String(36), sa.ForeignKey("mdm_imports.id"), nullable=False),
        sa.Column("left_record_id", sa.String(36), sa.ForeignKey("mdm_source_records.id"), nullable=False),
        sa.Column("right_record_id", sa.String(36), sa.ForeignKey("mdm_source_records.id"), nullable=False),
        sa.Column("engine_id", sa.String(80), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False))
    op.create_index("ix_mdm_candidates_import_id", "mdm_candidates", ["import_id"])
    op.create_table("mdm_golden_record_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("mdm_candidates.id"), unique=True, nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("mdm_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("mdm_candidates.id"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_mdm_audit_events_candidate_id", "mdm_audit_events", ["candidate_id"])


def downgrade():
    for table in ("mdm_audit_events", "mdm_golden_record_drafts", "mdm_candidates", "mdm_source_records", "mdm_imports"):
        op.drop_table(table)
