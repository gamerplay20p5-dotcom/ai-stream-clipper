"""access by streamer: an account sees every APPROVED clip of a streamer (now and future approvals)

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-20

Chain: ... 0011 (6C) -> 0012 (multi-user) -> 0013 (this). Single head.

A grant materialises ordinary ``clip_shares`` (same snapshot, protection and authorization code), each
tagged with ``grant_id``. Rollback safety: the downgrade keeps every row (the grants table is renamed
to ``streamer_grants_preserved``; ``clip_shares.grant_id`` is dropped, the shares themselves stay valid
explicit shares, so no account loses access silently).
"""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if 'streamer_grants_preserved' in tables:
        op.rename_table('streamer_grants_preserved', 'streamer_grants')
    else:
        op.create_table(
            'streamer_grants',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('streamer_id', sa.Integer(), sa.ForeignKey('streamers.id', ondelete='CASCADE'), nullable=False),
            sa.Column('can_download', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('revoked_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        )
        op.create_index('ix_streamer_grants_user', 'streamer_grants', ['user_id', 'revoked_at'])
        op.create_index('uq_streamer_grants_active', 'streamer_grants', ['user_id', 'streamer_id'], unique=True,
                        postgresql_where=sa.text('revoked_at IS NULL'), sqlite_where=sa.text('revoked_at IS NULL'))
    with op.batch_alter_table('clip_shares', schema=None) as b:
        b.add_column(sa.Column('grant_id', sa.Integer(), nullable=True))
        b.create_index('ix_clip_shares_grant', ['grant_id'])


def downgrade() -> None:
    with op.batch_alter_table('clip_shares', schema=None) as b:
        b.drop_index('ix_clip_shares_grant')
        b.drop_column('grant_id')
    op.rename_table('streamer_grants', 'streamer_grants_preserved')  # grant history is never dropped
