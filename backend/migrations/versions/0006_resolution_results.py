"""Persist resolution decisions, proposals, entities and history.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mdm_resolution_pairs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("left_record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id"), nullable=False),
        sa.Column("right_record_id", sa.String(36), sa.ForeignKey("mdm_resolution_records.id"), nullable=False),
        sa.Column("blocking_reasons", sa.JSON(), nullable=False),
        sa.Column("field_evidence", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("pair_decision", sa.String(24), nullable=False),
        sa.Column("decision_reason", sa.String(80), nullable=False),
        sa.Column("triage_status", sa.String(32), nullable=False),
        sa.Column("triage_reason", sa.String(80), nullable=False),
        sa.Column("user_decision", sa.String(24), nullable=False, server_default="UNDECIDED"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("run_id", "left_record_id", "right_record_id", name="uq_mdm_resolution_pair_records"),
    )
    op.create_index("ix_mdm_resolution_pairs_run_id", "mdm_resolution_pairs", ["run_id"])
    op.create_index("ix_mdm_resolution_pairs_run_triage", "mdm_resolution_pairs", ["run_id", "triage_status"])
    op.create_table(
        "mdm_resolution_review_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pair_ids", sa.JSON(), nullable=False),
        sa.Column("member_record_ids", sa.JSON(), nullable=False),
        sa.Column("primary_group", sa.String(48), nullable=False),
        sa.Column("subgroup", sa.String(48), nullable=False),
        sa.Column("triage_reason", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_mdm_resolution_review_cases_run_id", "mdm_resolution_review_cases", ["run_id"])
    op.create_index("ix_mdm_resolution_review_cases_run_category", "mdm_resolution_review_cases", ["run_id", "primary_group"])
    op.create_table(
        "mdm_resolution_proposals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_record_ids", sa.JSON(), nullable=False),
        sa.Column("golden_fields", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="AUTO_READY"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision_reason", sa.String(80), nullable=True),
    )
    op.create_index("ix_mdm_resolution_proposals_run_id", "mdm_resolution_proposals", ["run_id"])
    op.create_index("ix_mdm_resolution_proposals_run_status", "mdm_resolution_proposals", ["run_id", "status"])
    op.create_table(
        "mdm_resolution_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.String(64), sa.ForeignKey("mdm_resolution_proposals.id"), nullable=False),
        sa.Column("member_record_ids", sa.JSON(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "proposal_id", name="uq_mdm_resolution_entity_proposal"),
    )
    op.create_index("ix_mdm_resolution_entities_run_id", "mdm_resolution_entities", ["run_id"])
    op.create_table(
        "mdm_resolution_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.String(64), sa.ForeignKey("mdm_resolution_proposals.id"), nullable=False),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("mdm_resolution_entities.id"), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("before_fields", sa.JSON(), nullable=False),
        sa.Column("after_fields", sa.JSON(), nullable=False),
        sa.Column("member_record_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_history_run_id", "mdm_resolution_history", ["run_id"])
    op.create_index("ix_mdm_resolution_history_run_created", "mdm_resolution_history", ["run_id", "created_at"])
    op.create_table(
        "mdm_resolution_explanations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("primary_group", sa.String(48), nullable=False),
        sa.Column("subgroup", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("explanation", sa.JSON(), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "signature_hash", name="uq_mdm_resolution_explanation_signature"),
    )
    op.create_index("ix_mdm_resolution_explanations_run_id", "mdm_resolution_explanations", ["run_id"])


def downgrade():
    op.drop_index("ix_mdm_resolution_explanations_run_id", table_name="mdm_resolution_explanations")
    op.drop_table("mdm_resolution_explanations")
    op.drop_index("ix_mdm_resolution_history_run_created", table_name="mdm_resolution_history")
    op.drop_index("ix_mdm_resolution_history_run_id", table_name="mdm_resolution_history")
    op.drop_table("mdm_resolution_history")
    op.drop_index("ix_mdm_resolution_entities_run_id", table_name="mdm_resolution_entities")
    op.drop_table("mdm_resolution_entities")
    op.drop_index("ix_mdm_resolution_proposals_run_status", table_name="mdm_resolution_proposals")
    op.drop_index("ix_mdm_resolution_proposals_run_id", table_name="mdm_resolution_proposals")
    op.drop_table("mdm_resolution_proposals")
    op.drop_index("ix_mdm_resolution_review_cases_run_category", table_name="mdm_resolution_review_cases")
    op.drop_index("ix_mdm_resolution_review_cases_run_id", table_name="mdm_resolution_review_cases")
    op.drop_table("mdm_resolution_review_cases")
    op.drop_index("ix_mdm_resolution_pairs_run_triage", table_name="mdm_resolution_pairs")
    op.drop_index("ix_mdm_resolution_pairs_run_id", table_name="mdm_resolution_pairs")
    op.drop_table("mdm_resolution_pairs")
