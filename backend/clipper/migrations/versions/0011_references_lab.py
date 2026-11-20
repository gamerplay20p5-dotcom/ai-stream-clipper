"""phase 6C: references lab (items, collections) and visual format profiles

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19

Only new tables: the downgrade renames them to *_preserved (the owner's curated references and
profiles are never dropped by a rollback) and a later upgrade renames them back.
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None
TABLES = ('ref_collection_items', 'format_profiles', 'ref_collections', 'ref_items')


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if all(t + '_preserved' in existing for t in TABLES):
        for t in reversed(TABLES):
            op.rename_table(t + '_preserved', t)
        return
    op.create_table(
        'ref_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('url', sa.String(1024), nullable=False),
        sa.Column('external_id', sa.String(128), nullable=False),
        sa.Column('handle', sa.String(128), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('category', sa.String(64), nullable=False),
        sa.Column('games', sa.JSON(), nullable=False),
        sa.Column('humor', sa.JSON(), nullable=False),
        sa.Column('formats', sa.JSON(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('why', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('favorite', sa.Boolean(), nullable=False),
        sa.Column('image_path', sa.String(512), nullable=False),
        sa.Column('api_data', sa.JSON(), nullable=False),
        sa.Column('api_source', sa.String(64), nullable=False),
        sa.Column('api_fetched_at', sa.DateTime(), nullable=True),
        sa.Column('api_expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ref_items_external_id', 'ref_items', ['external_id'])
    op.create_index('ix_ref_items_api_expires_at', 'ref_items', ['api_expires_at'])
    op.create_table(
        'ref_collections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'ref_collection_items',
        sa.Column('collection_id', sa.Integer(), sa.ForeignKey('ref_collections.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('ref_items.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_table(
        'format_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('ref_items.id', ondelete='SET NULL'), nullable=True),
        sa.Column('image_path', sa.String(512), nullable=False),
        sa.Column('avatar_path', sa.String(512), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('analysis', sa.JSON(), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('favorite', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for t in TABLES:  # never drop the owner's curated data
        op.rename_table(t, t + '_preserved')
