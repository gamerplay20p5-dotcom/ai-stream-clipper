"""phase 6B: human comparisons between two edits + immutable media snapshot of each publication

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19

Rollback safety (docs/FASE6.md, "Rollback da 0010"):
* human votes are evidence: the downgrade renames ``edit_comparisons`` to
  ``edit_comparisons_preserved`` (unknown to older code, harmless) and a later upgrade renames it back;
* publication media snapshots (``publications.video_path``) are copied to
  ``publication_snapshots_preserved`` before the column is dropped and restored by a later upgrade.
  Older code does not read snapshots, so publishing must be PAUSED during a rollback and pending
  publications checked with ``clipper rollback-check`` before resuming.
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None
PRESERVED = 'edit_comparisons_preserved'
SNAPSHOTS = 'publication_snapshots_preserved'


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    if PRESERVED in tables:
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
    if SNAPSHOTS in tables:  # snapshots kept by an earlier downgrade come back
        op.execute(sa.text(
            "UPDATE publications SET video_path = (SELECT s.video_path FROM " + SNAPSHOTS + " s "
            "WHERE s.publication_id = publications.id) "
            "WHERE id IN (SELECT publication_id FROM " + SNAPSHOTS + ")"))
        op.drop_table(SNAPSHOTS)


def downgrade() -> None:
    op.create_table(SNAPSHOTS, sa.Column('publication_id', sa.Integer(), primary_key=True),
                    sa.Column('video_path', sa.String(512), nullable=False))
    op.execute(sa.text("INSERT INTO " + SNAPSHOTS + " (publication_id, video_path) "
                       "SELECT id, video_path FROM publications WHERE video_path <> ''"))
    with op.batch_alter_table('publications', schema=None) as b:
        b.drop_column('video_path')
    op.rename_table('edit_comparisons', PRESERVED)  # never drop human votes
