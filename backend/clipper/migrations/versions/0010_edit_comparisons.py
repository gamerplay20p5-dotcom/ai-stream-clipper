"""phase 6B: human comparisons between two edits of the same clip

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'edit_comparisons',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clip_id', sa.Integer(), sa.ForeignKey('clips.id', ondelete='CASCADE'), nullable=False),
        sa.Column('left', sa.String(16), nullable=False),
        sa.Column('right', sa.String(16), nullable=False),
        sa.Column('left_structure', sa.String(8), nullable=False),
        sa.Column('right_structure', sa.String(8), nullable=False),
        sa.Column('winner', sa.String(8), nullable=False),
        sa.Column('blind', sa.Boolean(), nullable=False),
        sa.Column('reviewer', sa.String(255), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_edit_comparisons_clip_id', 'edit_comparisons', ['clip_id'])


def downgrade() -> None:
    op.drop_table('edit_comparisons')
