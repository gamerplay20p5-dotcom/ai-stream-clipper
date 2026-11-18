"""phase 4: auto publisher (apps, oauth states, accounts, rules, publications)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'publish_apps',
        sa.Column('platform', sa.String(20), primary_key=True),
        sa.Column('client_id', sa.String(512), nullable=False),
        sa.Column('client_secret_enc', sa.Text(), nullable=False),
        sa.Column('redirect_uri', sa.String(1024), nullable=False),
        sa.Column('updated_by', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'oauth_states',
        sa.Column('state', sa.String(64), primary_key=True),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('verifier_enc', sa.Text(), nullable=False),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'publish_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('handle', sa.String(255), nullable=False),
        sa.Column('tokens_enc', sa.Text(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('quota', sa.JSON(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=False),
        sa.Column('connected_by', sa.String(255), nullable=False),
        sa.Column('connected_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('platform', 'external_id', name='uq_publish_account'),
    )
    op.create_index('ix_publish_accounts_platform', 'publish_accounts', ['platform'])
    op.create_table(
        'publish_rules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('publish_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('streamer_id', sa.Integer(), sa.ForeignKey('streamers.id', ondelete='CASCADE'), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('require_approval', sa.Boolean(), nullable=False),
        sa.Column('min_score', sa.Float(), nullable=False),
        sa.Column('privacy', sa.String(16), nullable=False),
        sa.Column('allow_public_auto', sa.Boolean(), nullable=False),
        sa.Column('max_per_day', sa.Integer(), nullable=False),
        sa.Column('slots', sa.JSON(), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False),
        sa.Column('templates', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_publish_rules_account_id', 'publish_rules', ['account_id'])
    op.create_table(
        'publications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clip_id', sa.Integer(), sa.ForeignKey('clips.id', ondelete='SET NULL'), nullable=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('publish_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rule_id', sa.Integer(), sa.ForeignKey('publish_rules.id', ondelete='SET NULL'), nullable=True),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('origin', sa.String(16), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('privacy', sa.String(16), nullable=False),
        sa.Column('video_sha256', sa.String(64), nullable=False),
        sa.Column('video_bytes', sa.Integer(), nullable=False),
        sa.Column('upload_state', sa.JSON(), nullable=False),
        sa.Column('remote_id', sa.String(255), nullable=False),
        sa.Column('remote_url', sa.String(1024), nullable=False),
        sa.Column('remote_status', sa.JSON(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('metrics_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=False),
        sa.Column('log', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('clip_id', 'account_id', name='uq_publication_clip_account'),
    )
    for col in ('clip_id', 'account_id', 'platform', 'status', 'scheduled_at'):
        op.create_index(f'ix_publications_{col}', 'publications', [col])


def downgrade() -> None:
    op.drop_table('publications')
    op.drop_table('publish_rules')
    op.drop_table('publish_accounts')
    op.drop_table('oauth_states')
    op.drop_table('publish_apps')
