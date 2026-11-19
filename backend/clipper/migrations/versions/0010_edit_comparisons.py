"""phase 6B: human comparisons between two edits + immutable media snapshot of each publication

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19

Rollback safety: human votes are evidence. The downgrade never drops them - the table is renamed
to ``edit_comparisons_preserved`` (unknown to older code, harmless) and a later upgrade renames it
back. ``publications.video_path`` is only a snapshot pointer; older code ignores it.
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None
PRESERVED = 'edit_comparisons_preserved'


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if PRESERVED in insp.get_table_names():
        op.rename_table(PRESERVED, 'edit_comparisons')  # votes kept by an earlier downgrade come back
    else:
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
    with op.batch_alter_table('publications', schema=None) as b:
        b.add_column(sa.Column('video_path', sa.String(512), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('publications', schema=None) as b:
        b.drop_column('video_path')
    op.rename_table('edit_comparisons', PRESERVED)  # never drop human votes
