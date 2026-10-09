"""LLM audit tables — Phase L4.

Revision ID: 0013_llm_audit_tables
Revises: 0012_user_role

Tables:
  llm_requests         — audit record mỗi OpenRouter call
  llm_semantic_evidence — evidence đã map từ LLM semantic fallback
  llm_explanations     — explanation cache được persist (pair + group)
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_llm_audit_tables"
down_revision = "0012_user_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── llm_requests ──────────────────────────────────────────────────────────
    op.create_table(
        "llm_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_type", sa.String(50), nullable=False),
        sa.Column("pair_id", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="openrouter"),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_llm_requests_request_type", "llm_requests", ["request_type"])
    op.create_index("ix_llm_requests_created_at", "llm_requests", ["created_at"])
    op.create_index("ix_llm_requests_status", "llm_requests", ["status"])

    # ── llm_semantic_evidence ─────────────────────────────────────────────────
    op.create_table(
        "llm_semantic_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pair_id", sa.String(100), nullable=False),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("semantic_result", sa.String(50), nullable=False),
        sa.Column("mapped_state", sa.String(10), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=True),
        sa.Column("short_reason", sa.String(240), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("map_version", sa.String(50), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column(
            "eligible_for_auto_merge",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_llm_semantic_evidence_pair_id", "llm_semantic_evidence", ["pair_id"])

    # ── llm_explanations ──────────────────────────────────────────────────────
    op.create_table(
        "llm_explanations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_type", sa.String(30), nullable=False),   # "pair" | "group"
        sa.Column("scope_id", sa.String(100), nullable=False),    # pair_id hoặc "group/subgroup"
        sa.Column("explanation_json", sa.JSON(), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_llm_explanations_scope",
        "llm_explanations",
        ["scope_type", "scope_id"],
    )
    op.create_index(
        "ix_llm_explanations_signature",
        "llm_explanations",
        ["signature_hash"],
    )


def downgrade() -> None:
    op.drop_table("llm_explanations")
    op.drop_table("llm_semantic_evidence")
    op.drop_table("llm_requests")
