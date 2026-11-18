"""diagnostics stats and manual test clips

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stream_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stats', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_test', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_index('ix_clips_is_test', ['is_test'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.drop_index('ix_clips_is_test')
        batch_op.drop_column('is_test')
    with op.batch_alter_table('stream_sessions', schema=None) as batch_op:
        batch_op.drop_column('stats')
