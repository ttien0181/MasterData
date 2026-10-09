"""Resumable resolution uploads and session lifecycle metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0018_upload_sessions"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mdm_resolution_runs") as batch:
        batch.add_column(sa.Column("name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column("record_count", existing_type=sa.Integer(), type_=sa.BigInteger(), nullable=False)
    with op.batch_alter_table("mdm_resolution_files") as batch:
        batch.alter_column("row_count", existing_type=sa.Integer(), type_=sa.BigInteger(), nullable=False)

    op.create_table(
        "mdm_resolution_upload_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("storage_backend", sa.String(length=12), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mdm_resolution_upload_sessions_owner_user_id", "mdm_resolution_upload_sessions", ["owner_user_id"])
    op.create_index("ix_mdm_upload_sessions_owner_status", "mdm_resolution_upload_sessions", ["owner_user_id", "status", "created_at"])

    with op.batch_alter_table("mdm_resolution_runs") as batch:
        batch.add_column(sa.Column("upload_session_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_mdm_run_upload_session", "mdm_resolution_upload_sessions",
                                 ["upload_session_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_mdm_run_upload_session", ["upload_session_id"])

    op.create_table(
        "mdm_resolution_upload_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("mdm_resolution_upload_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("replaces_file_id", sa.String(length=36), sa.ForeignKey("mdm_resolution_upload_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("received_size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("source_counts", sa.JSON(), nullable=False),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "position", name="uq_mdm_upload_file_position"),
        sa.UniqueConstraint("replaces_file_id", name="uq_mdm_upload_file_replacement"),
    )
    op.create_index("ix_mdm_resolution_upload_files_session_id", "mdm_resolution_upload_files", ["session_id"])
    op.create_index("ix_mdm_upload_files_session", "mdm_resolution_upload_files", ["session_id", "position"])

    op.create_table(
        "mdm_resolution_upload_parts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("upload_file_id", sa.String(length=36), sa.ForeignKey("mdm_resolution_upload_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part_number", sa.BigInteger(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("upload_file_id", "part_number", name="uq_mdm_upload_part_number"),
    )
    op.create_index("ix_mdm_resolution_upload_parts_upload_file_id", "mdm_resolution_upload_parts", ["upload_file_id"])
    op.create_index("ix_mdm_upload_parts_file", "mdm_resolution_upload_parts", ["upload_file_id", "part_number"])


def downgrade() -> None:
    with op.batch_alter_table("mdm_resolution_runs") as batch:
        batch.drop_constraint("uq_mdm_run_upload_session", type_="unique")
        batch.drop_constraint("fk_mdm_run_upload_session", type_="foreignkey")
        batch.drop_column("upload_session_id")
    op.drop_index("ix_mdm_upload_parts_file", table_name="mdm_resolution_upload_parts")
    op.drop_index("ix_mdm_resolution_upload_parts_upload_file_id", table_name="mdm_resolution_upload_parts")
    op.drop_table("mdm_resolution_upload_parts")
    op.drop_index("ix_mdm_upload_files_session", table_name="mdm_resolution_upload_files")
    op.drop_index("ix_mdm_resolution_upload_files_session_id", table_name="mdm_resolution_upload_files")
    op.drop_table("mdm_resolution_upload_files")
    op.drop_index("ix_mdm_upload_sessions_owner_status", table_name="mdm_resolution_upload_sessions")
    op.drop_index("ix_mdm_resolution_upload_sessions_owner_user_id", table_name="mdm_resolution_upload_sessions")
    op.drop_table("mdm_resolution_upload_sessions")
    with op.batch_alter_table("mdm_resolution_files") as batch:
        batch.alter_column("row_count", existing_type=sa.BigInteger(), type_=sa.Integer(), nullable=False)
    with op.batch_alter_table("mdm_resolution_runs") as batch:
        batch.alter_column("record_count", existing_type=sa.BigInteger(), type_=sa.Integer(), nullable=False)
        batch.drop_column("archived_at")
        batch.drop_column("name")
