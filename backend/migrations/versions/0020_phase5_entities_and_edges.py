"""TASK-5.1/5.2/5.3/6.2: Entity tables, negative edges, golden field values,
and four-eyes review columns (§6.3, §SVC-02-FR-006).
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_phase5_entities_and_edges"
down_revision = "0019_upload_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TASK-5.1: persistent entities
    op.create_table(
        "mdm_resolution_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("entity_type", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_entities_run", "mdm_resolution_entities", ["run_id"])
    op.create_index("ix_mdm_resolution_entities_status", "mdm_resolution_entities", ["run_id", "status"])

    op.create_table(
        "mdm_resolution_entity_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("mdm_resolution_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("added_by", sa.String(36), nullable=False, server_default="SYSTEM"),
        sa.UniqueConstraint("entity_id", "record_id", name="uq_mdm_entity_member"),
    )
    op.create_index("ix_mdm_entity_members_record", "mdm_resolution_entity_members", ["record_id"])

    op.create_table(
        "mdm_resolution_entity_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("mdm_resolution_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("golden_fields", sa.JSON, nullable=False),
        sa.Column("changed_by", sa.String(36), nullable=False, server_default="SYSTEM"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entity_id", "version", name="uq_mdm_entity_version"),
    )
    op.create_index("ix_mdm_entity_versions_entity", "mdm_resolution_entity_versions", ["entity_id"])

    # TASK-5.2: negative edges
    op.create_table(
        "mdm_resolution_negative_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("left_record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id"), nullable=False),
        sa.Column("right_record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id"), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_negative_edges_run", "mdm_resolution_negative_edges", ["run_id"])
    op.create_index("ix_mdm_negative_edges_pair", "mdm_resolution_negative_edges",
                    ["run_id", "left_record_id", "right_record_id"])

    # TASK-5.3: golden field values
    op.create_table(
        "mdm_resolution_golden_field_values",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("mdm_resolution_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("display_value", sa.Text, nullable=False),
        sa.Column("source_record_id", sa.String(36), nullable=True),
        sa.Column("provenance", sa.String(24), nullable=False, server_default="SOURCE_PRIORITY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entity_id", "version", "field_name", name="uq_mdm_golden_field"),
    )
    op.create_index("ix_mdm_golden_field_values_entity", "mdm_resolution_golden_field_values",
                    ["entity_id", "version"])

    # TASK-8.1: stage result persistence
    op.create_table(
        "mdm_resolution_stage_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.String(80), nullable=False),
        sa.Column("stage_name", sa.String(24), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metrics", sa.JSON, nullable=False),
        sa.Column("output_artifacts", sa.JSON, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "attempt_id", "stage_name", name="uq_mdm_stage_result"),
    )
    op.create_index("ix_mdm_stage_results_run", "mdm_resolution_stage_results", ["run_id", "attempt_id"])

    # TASK-6.2: four-eyes review columns on ResolutionReviewCase
    inspector = sa.inspect(op.get_bind())
    existing_cols = {c["name"] for c in inspector.get_columns("mdm_resolution_review_cases")}
    four_eyes_cols = [
        ("first_reviewer_id", sa.String(36)),
        ("first_decision", sa.String(24)),
        ("first_decided_at", sa.DateTime(timezone=True)),
        ("second_reviewer_id", sa.String(36)),
        ("second_decision", sa.String(24)),
        ("second_decided_at", sa.DateTime(timezone=True)),
    ]
    for col_name, col_type in four_eyes_cols:
        if col_name not in existing_cols:
            op.add_column("mdm_resolution_review_cases",
                          sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    for col in ("second_decided_at", "second_decision", "second_reviewer_id",
                "first_decided_at", "first_decision", "first_reviewer_id"):
        op.drop_column("mdm_resolution_review_cases", col)
    op.drop_table("mdm_resolution_stage_results")
    op.drop_table("mdm_resolution_golden_field_values")
    op.drop_table("mdm_resolution_negative_edges")
    op.drop_table("mdm_resolution_entity_versions")
    op.drop_table("mdm_resolution_entity_members")
    op.drop_table("mdm_resolution_entities")
