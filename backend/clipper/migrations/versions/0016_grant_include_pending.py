"""access by streamer may include clips still waiting for review (not only the approved ones)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-20

``include_pending`` defaults to false (previous behaviour: approved clips only). Rollback: the downgrade
drops the column; shares already created stay valid explicit-by-grant shares.
"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('streamer_grants')}
    if 'include_pending' not in cols:
        with op.batch_alter_table('streamer_grants') as b:
            b.add_column(sa.Column('include_pending', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('streamer_grants') as b:
        b.drop_column('include_pending')
