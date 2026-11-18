"""phase 5 stage E: speaker labels on words, speech turns, streamer voice profiles

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('transcript_words', schema=None) as b:
        b.add_column(sa.Column('speaker', sa.String(16), nullable=False, server_default=''))
    op.create_table(
        'speech_turns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('stream_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('segment_idx', sa.Integer(), nullable=False),
        sa.Column('start', sa.Float(), nullable=False),
        sa.Column('end', sa.Float(), nullable=False),
        sa.Column('emb', sa.JSON(), nullable=False),
        sa.Column('label', sa.String(16), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
    )
    op.create_index('ix_speech_turns_session_start', 'speech_turns', ['session_id', 'start'])
    op.create_table(
        'speaker_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('streamer_id', sa.Integer(), sa.ForeignKey('streamers.id', ondelete='CASCADE'), nullable=False,
                  unique=True),
        sa.Column('centroid', sa.JSON(), nullable=False),
        sa.Column('n', sa.Integer(), nullable=False),
        sa.Column('confirmed', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('sample', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('speaker_profiles')
    op.drop_table('speech_turns')
    with op.batch_alter_table('transcript_words', schema=None) as b:
        b.drop_column('speaker')
