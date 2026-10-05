"""Retire the prototype workflows and browser-owned choice drafts.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

RETIRED = (
    "mdm_resolution_history",
    "mdm_resolution_entities",
    "mdm_resolution_request_keys",
    "mdm_resolution_previews",
    "mdm_resolution_choice_drafts",
    "mdm_audit_events",
    "mdm_golden_record_drafts",
    "mdm_candidates",
    "mdm_source_records",
    "mdm_imports",
)


def upgrade():
    for table in RETIRED:
        op.drop_table(table)
    # Keep pair decisions and IDs; historical auto rejects no longer need a full signal snapshot.
    op.execute("UPDATE mdm_resolution_pairs SET field_evidence = '[]' "
               "WHERE pair_decision = 'NON_MATCH' AND field_evidence::text <> '[]'")


def downgrade():
    raise NotImplementedError("Retired prototype data cannot be restored by this migration")
