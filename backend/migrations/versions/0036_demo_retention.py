"""Allow cleanup exclusively for revoked, expired synthetic demo identities."""
from alembic import op

revision='0036_demo_retention'
down_revision='0035_demo_grants'
branch_labels=depends_on=None


def upgrade():
    from masterone.mdm.demo_retention import install
    install(op.get_bind())


def downgrade():
    from masterone.mdm.demo_retention import install
    install(op.get_bind(),enabled=False)
