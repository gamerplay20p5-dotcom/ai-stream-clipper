"""phase 6A: quality gate outcome on clips

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clips', schema=None) as b:
        b.add_column(sa.Column('gate', sa.String(20), nullable=False, server_default=''))
        b.create_index('ix_clips_gate', ['gate'])


def downgrade() -> None:
    with op.batch_alter_table('clips', schema=None) as b:
        b.drop_index('ix_clips_gate')
        b.drop_column('gate')
