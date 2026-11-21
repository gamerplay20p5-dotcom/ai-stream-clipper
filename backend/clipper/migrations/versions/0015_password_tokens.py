"""single-use password links (invite / reset); only the SHA-256 of each token is stored

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-20

Chain: ... 0013 (streamer grants) -> 0014 (per_slot) -> 0015 (this). Single head.
Rollback safety: the downgrade drops only this table (pending links stop working; passwords are untouched).
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'password_tokens' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'password_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('created_by', sa.String(255), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_password_tokens_token_hash', 'password_tokens', ['token_hash'], unique=True)
    op.create_index('ix_password_tokens_user_id', 'password_tokens', ['user_id'])


def downgrade() -> None:
    op.drop_table('password_tokens')
