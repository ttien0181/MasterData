"""Reconcile upload links missing from early deployments of revision 0018.

Some databases applied 0018 before the run/draft and replacement links were
added to that revision. A new revision is required: upgrading to an already
stamped revision does not execute its changed migration file again.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_upload_links"
down_revision = "0018_upload_sessions"
branch_labels = None
depends_on = None


def _ensure_link(table: str, column: str, target: str, fk_name: str, unique_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    has_column = any(item["name"] == column for item in inspector.get_columns(table))
    has_fk = any(
        item["constrained_columns"] == [column]
        and item["referred_table"] == target
        and item["referred_columns"] == ["id"]
        for item in inspector.get_foreign_keys(table)
    )
    has_unique = any(
        item["column_names"] == [column]
        for item in inspector.get_unique_constraints(table)
    )
    if has_column and has_fk and has_unique:
        return
    with op.batch_alter_table(table) as batch:
        if not has_column:
            batch.add_column(sa.Column(column, sa.String(length=36), nullable=True))
        if not has_fk:
            batch.create_foreign_key(fk_name, target, [column], ["id"], ondelete="SET NULL")
        if not has_unique:
            batch.create_unique_constraint(unique_name, [column])


def upgrade() -> None:
    _ensure_link(
        "mdm_resolution_runs", "upload_session_id", "mdm_resolution_upload_sessions",
        "fk_mdm_run_upload_session", "uq_mdm_run_upload_session",
    )
    _ensure_link(
        "mdm_resolution_upload_files", "replaces_file_id", "mdm_resolution_upload_files",
        "fk_mdm_upload_file_replacement", "uq_mdm_upload_file_replacement",
    )


def downgrade() -> None:
    # Current 0018 already defines these columns. Preserve them and their data
    # when returning to that revision, irrespective of its deployed variant.
    pass
