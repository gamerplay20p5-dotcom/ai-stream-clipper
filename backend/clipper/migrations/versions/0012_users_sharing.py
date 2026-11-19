"""multi-user stage 1: session versions on users + clip shares with immutable media snapshots

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19

Chain: ... 0010 (6B) -> 0011 (6C references lab) -> 0012 (this). Single head.

Rollback safety: shares are an access history: the downgrade renames ``clip_shares`` to
``clip_shares_preserved`` and a later upgrade renames it back. ``users.session_version`` is dropped
(older code does not read it); ``users.role`` is NOT changed by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as b:
        b.add_column(sa.Column('session_version', sa.Integer(), nullable=False, server_default='0'))
    if 'clip_shares_preserved' in sa.inspect(op.get_bind()).get_table_names():
        op.rename_table('clip_shares_preserved', 'clip_shares')
        return
    op.create_table(
        'clip_shares',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clip_id', sa.Integer(), sa.ForeignKey('clips.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('can_view', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('can_download', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('can_variants', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('video_path', sa.String(512), nullable=False, server_default=''),
        sa.Column('video_sha256', sa.String(64), nullable=False, server_default=''),
        sa.Column('video_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('thumb_path', sa.String(512), nullable=False, server_default=''),
        sa.Column('clip_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('variants', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('follow_latest', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_clip_shares_clip_id', 'clip_shares', ['clip_id'])
    op.create_index('ix_clip_shares_user_active', 'clip_shares', ['user_id', 'revoked_at'])
    op.create_index('uq_clip_shares_active', 'clip_shares', ['clip_id', 'user_id'], unique=True,
                    postgresql_where=sa.text('revoked_at IS NULL'), sqlite_where=sa.text('revoked_at IS NULL'))


def downgrade() -> None:
    op.rename_table('clip_shares', 'clip_shares_preserved')  # access history is never dropped
    with op.batch_alter_table('users', schema=None) as b:
        b.drop_column('session_version')
