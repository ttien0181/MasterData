"""Durable LLM policy: shared switch, Gemini cooldown, per-request reservations and accounting indexes.

Accounting rows are never deleted by downgrade: budget history must survive a rollback.
"""
import sqlalchemy as sa
from alembic import op

revision = "0027_llm_policy"
down_revision = "0026_run_summaries"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "llm_policy_control" not in tables:
        control = op.create_table(
            "llm_policy_control",
            sa.Column("id", sa.String(24), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("gemini_blocked_until", sa.DateTime(timezone=True), nullable=True),
        )
        # Fail closed: a fresh database starts with the shared switch OFF.
        op.bulk_insert(control, [{"id": "global", "enabled": False, "revision": 1, "gemini_blocked_until": None}])
    if "llm_reservations" not in tables:
        op.create_table(
            "llm_reservations",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("run_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("purpose", sa.String(24), nullable=False),
            sa.Column("context_hash", sa.String(64), nullable=False),
            sa.Column("context", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("calls", sa.Integer(), nullable=False),
            sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
            sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=False),
            sa.Column("response", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_llm_reservations_run_id", "llm_reservations", ["run_id"])
        op.create_index("ix_llm_reservations_user_id", "llm_reservations", ["user_id"])
        op.create_index("ix_llm_reservations_state", "llm_reservations", ["state"])
    existing_indexes = {index["name"] for index in inspector.get_indexes("llm_requests")}
    if "ix_llm_requests_created_status" not in existing_indexes:
        op.create_index("ix_llm_requests_created_status", "llm_requests", ["created_at", "status"])


def downgrade():
    op.drop_index("ix_llm_requests_created_status", table_name="llm_requests")
