"""phase 5: editorial scores, storage lifecycle, situations, cleanup log, learning runs, review source

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clips', schema=None) as b:
        b.add_column(sa.Column('prelim_score', sa.Float(), nullable=True))
        b.add_column(sa.Column('editorial_score', sa.Float(), nullable=True))
        b.add_column(sa.Column('confidence', sa.Float(), nullable=True))
        b.add_column(sa.Column('situation_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('protected', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('favorite', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('lifecycle', sa.String(16), nullable=False, server_default='active'))
        b.add_column(sa.Column('quarantined_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('purge_after', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('purge_reason', sa.String(255), nullable=False, server_default=''))
        b.add_column(sa.Column('media_purged_at', sa.DateTime(), nullable=True))
        b.create_index('ix_clips_situation_id', ['situation_id'])
        b.create_index('ix_clips_lifecycle', ['lifecycle'])
    with op.batch_alter_table('reviews', schema=None) as b:
        b.add_column(sa.Column('source', sa.String(16), nullable=False, server_default='review'))
    op.create_table(
        'situations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('stream_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('start', sa.Float(), nullable=False),
        sa.Column('last', sa.Float(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('related_to', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_situations_session_start', 'situations', ['session_id', 'start'])
    op.create_index('ix_situations_kind', 'situations', ['kind'])
    op.create_table(
        'cleanup_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clip_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('reason', sa.String(255), nullable=False),
        sa.Column('bytes_freed', sa.Integer(), nullable=False),
        sa.Column('dry_run', sa.Boolean(), nullable=False),
        sa.Column('actor', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_cleanup_log_clip_id', 'cleanup_log', ['clip_id'])
    op.create_index('ix_cleanup_log_created_at', 'cleanup_log', ['created_at'])
    op.create_table(
        'learning_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('labels', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(32), nullable=False),
        sa.Column('ranker_id', sa.Integer(), nullable=True),
        sa.Column('profile', sa.JSON(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_learning_runs_created_at', 'learning_runs', ['created_at'])


def downgrade() -> None:
    op.drop_table('learning_runs')
    op.drop_table('cleanup_log')
    op.drop_table('situations')
    with op.batch_alter_table('reviews', schema=None) as b:
        b.drop_column('source')
    with op.batch_alter_table('clips', schema=None) as b:
        b.drop_index('ix_clips_lifecycle')
        b.drop_index('ix_clips_situation_id')
        for col in ('media_purged_at', 'purge_reason', 'purge_after', 'quarantined_at', 'lifecycle', 'favorite',
                    'protected', 'situation_id', 'confidence', 'editorial_score', 'prelim_score'):
            b.drop_column(col)
