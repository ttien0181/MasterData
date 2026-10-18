"""Temporary, scoped product demonstration sessions."""
import sqlalchemy as sa
from alembic import op

revision = '0035_demo_grants'
down_revision = '0034_pair_endpoint_index'
branch_labels = depends_on = None


def upgrade():
    op.create_table('demo_grants',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('session_id', sa.String(36), sa.ForeignKey('auth_sessions.id'), nullable=False, unique=True),
        sa.Column('run_id', sa.String(36), nullable=True, unique=True),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('dataset_version', sa.String(40), nullable=False),
        sa.Column('expires_at_epoch', sa.Integer(), nullable=False),
        sa.Column('last_seen_epoch', sa.Integer(), nullable=False),
        sa.Column('created_at_epoch', sa.Integer(), nullable=False))
    op.create_index('ix_demo_grants_expires_at_epoch', 'demo_grants', ['expires_at_epoch'])


def downgrade():
    op.drop_table('demo_grants')
