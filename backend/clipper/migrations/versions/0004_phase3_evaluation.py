"""phase 3: candidates, evaluation examples, human reviews, rankers, clip provenance

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provenance', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    op.create_table(
        'candidates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('stream_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('streamer_id', sa.Integer(), sa.ForeignKey('streamers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('peak', sa.Float(), nullable=False),
        sa.Column('start', sa.Float(), nullable=True),
        sa.Column('end', sa.Float(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('decision', sa.String(24), nullable=False),
        sa.Column('kinds', sa.JSON(), nullable=False),
        sa.Column('breakdown', sa.JSON(), nullable=False),
        sa.Column('features', sa.JSON(), nullable=False),
        sa.Column('transcript', sa.Text(), nullable=False),
        sa.Column('situation', sa.String(64), nullable=False),
        sa.Column('clip_id', sa.Integer(), sa.ForeignKey('clips.id', ondelete='SET NULL'), nullable=True),
        sa.Column('scoring_version', sa.String(32), nullable=False),
        sa.Column('ranker_score', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_candidates_session_peak', 'candidates', ['session_id', 'peak'])
    op.create_index('ix_candidates_streamer_id', 'candidates', ['streamer_id'])
    op.create_index('ix_candidates_decision', 'candidates', ['decision'])
    op.create_index('ix_candidates_clip_id', 'candidates', ['clip_id'])
    op.create_index('ix_candidates_created_at', 'candidates', ['created_at'])

    op.create_table(
        'eval_examples',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_id', sa.Integer(), sa.ForeignKey('candidates.id', ondelete='SET NULL'), nullable=True,
                  unique=True),
        sa.Column('clip_id', sa.Integer(), sa.ForeignKey('clips.id', ondelete='SET NULL'), nullable=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('stream_sessions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('streamer_id', sa.Integer(), sa.ForeignKey('streamers.id', ondelete='SET NULL'), nullable=True),
        sa.Column('streamer_name', sa.String(255), nullable=False),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('stratum', sa.String(32), nullable=False),
        sa.Column('priority', sa.Float(), nullable=False),
        sa.Column('split', sa.String(8), nullable=False),
        sa.Column('is_test', sa.Boolean(), nullable=False),
        sa.Column('media_start', sa.Float(), nullable=False),
        sa.Column('media_end', sa.Float(), nullable=False),
        sa.Column('ai', sa.JSON(), nullable=False),
        sa.Column('context', sa.JSON(), nullable=False),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.Column('ai_runs', sa.JSON(), nullable=False),
        sa.Column('media_state', sa.String(16), nullable=False),
        sa.Column('media_path', sa.String(512), nullable=False),
        sa.Column('media_bytes', sa.Integer(), nullable=False),
        sa.Column('media_error', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    )
    for col in ('clip_id', 'session_id', 'stratum', 'split', 'media_state', 'created_at'):
        op.create_index(f'ix_eval_examples_{col}', 'eval_examples', [col])

    op.create_table(
        'reviews',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('example_id', sa.Integer(), sa.ForeignKey('eval_examples.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reviewer', sa.String(255), nullable=False),
        sa.Column('rating', sa.String(24), nullable=False),
        sa.Column('category', sa.String(32), nullable=False),
        sa.Column('editorial_reasons', sa.JSON(), nullable=False),
        sa.Column('technical_issues', sa.JSON(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('context_quality', sa.Integer(), nullable=True),
        sa.Column('ideal_duration', sa.Float(), nullable=True),
        sa.Column('event_start', sa.Float(), nullable=True),
        sa.Column('event_peak', sa.Float(), nullable=True),
        sa.Column('event_end', sa.Float(), nullable=True),
        sa.Column('short_start', sa.Float(), nullable=True),
        sa.Column('short_end', sa.Float(), nullable=True),
        sa.Column('ai_snapshot', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('example_id', 'reviewer', name='uq_review_example_reviewer'),
    )
    op.create_index('ix_reviews_example_id', 'reviews', ['example_id'])

    op.create_table(
        'ranker_models',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('feature_names', sa.JSON(), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('n_train', sa.Integer(), nullable=False),
        sa.Column('n_val', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_ranker_models_status', 'ranker_models', ['status'])


def downgrade() -> None:
    op.drop_table('ranker_models')
    op.drop_table('reviews')
    op.drop_table('eval_examples')
    op.drop_table('candidates')
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.drop_column('provenance')
