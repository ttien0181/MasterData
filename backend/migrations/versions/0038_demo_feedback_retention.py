"""Include run-scoped feedback in expired synthetic demo cleanup."""
from alembic import op

revision='0038_demo_feedback_retention'
down_revision='0037_demo_cleanup_indexes'
branch_labels=depends_on=None


def upgrade():
    from masterone.mdm.demo_retention import install
    install(op.get_bind())


def downgrade():
    from masterone.mdm.demo_retention import install
    install(op.get_bind(),enabled=False)
