"""Preserve snapshot IDs; scope source identity and add lossless import metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0021_input_contract"
down_revision = "0020_phase5_entities_and_edges"
branch_labels = None
depends_on = None


def upgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("mdm_resolution_records")}
    with op.batch_alter_table("mdm_resolution_records") as batch:
        for name in ("original_fields", "mapping_provenance", "lineage"):
            if name not in existing:
                batch.add_column(sa.Column(name, sa.JSON(), nullable=True))
        batch.drop_constraint("uq_mdm_resolution_record_external", type_="unique")
        batch.create_unique_constraint("uq_mdm_resolution_record_source_external",
                                       ["run_id", "source_system", "external_id"])
    # Historical raw text and row positions cannot be reconstructed reliably.
    # NULL metadata explicitly means legacy/unavailable; IDs and JSON stay intact.


def downgrade():
    bind = op.get_bind()
    collision = bind.execute(sa.text(
        "SELECT run_id FROM mdm_resolution_records GROUP BY run_id, external_id "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if collision:
        raise RuntimeError("Cannot downgrade: source namespaces contain overlapping IDs")
    with op.batch_alter_table("mdm_resolution_records") as batch:
        batch.drop_constraint("uq_mdm_resolution_record_source_external", type_="unique")
        batch.create_unique_constraint("uq_mdm_resolution_record_external", ["run_id", "external_id"])
        # Old runtime ignores these nullable columns. Retain imported raw values
        # and provenance on rollback; re-upgrade reuses the additive columns.
