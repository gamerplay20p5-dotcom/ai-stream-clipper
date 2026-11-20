"""publish rules: how many videos may share the same calendar slot (peak-hour bursts)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-20

Chain: ... 0012 (multi-user) -> 0013 (streamer grants) -> 0014 (this). Single head.

``per_slot`` defaults to 1 (the previous behaviour: one publication per slot). Rollback safety: the
downgrade only drops this column; rules keep their slots and daily limit.
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('publish_rules')}
    if 'per_slot' not in cols:
        with op.batch_alter_table('publish_rules') as b:
            b.add_column(sa.Column('per_slot', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    with op.batch_alter_table('publish_rules') as b:
        b.drop_column('per_slot')
