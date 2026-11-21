"""shared lives: one capture per live, individual monitoring subscriptions per customer

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-21

* ``channel_monitors``: a customer follows a source channel (``streamers``) with its own editorial preset.
  Many customers can follow the same channel; at most one active monitor per (user, channel).
* ``monitor_sessions``: one customer attached to one live session, with the window of the live it is
  entitled to (``from_t``..``until_t``) and why it stopped (hours, access, plan). Unique per (monitor, session).
* ``streamers.hts_monitoring``: the HTS itself consumes the channel (true for every existing channel, so HTS
  behaviour is unchanged); channels added only by customers are false and are captured while someone needs them.
* ``stream_sessions.base_profile``: analysis profile (language, models) pinned when the live starts, so every
  consumer reuses the same base analysis and a settings change never mixes incompatible results.
* ``clip_shares.monitor_id``: shares created for a monitor (the customer's library); same authorization,
  snapshot and cleanup protection as every other share.

Data: each channel that PR #23 gave to a customer (``owner_id``) becomes a monitor of that customer, and the
access-by-streamer the owner received is closed (its clips stay in the library). ``owner_id`` is kept, unused.
Additive only. The downgrade drops what this revision added.
"""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    streamer_cols = {c['name'] for c in inspector.get_columns('streamers')}
    if 'hts_monitoring' not in streamer_cols:
        with op.batch_alter_table('streamers') as b:
            b.add_column(sa.Column('hts_monitoring', sa.Boolean(), nullable=False, server_default=sa.true()))
    session_cols = {c['name'] for c in inspector.get_columns('stream_sessions')}
    if 'base_profile' not in session_cols:
        with op.batch_alter_table('stream_sessions') as b:
            b.add_column(sa.Column('base_profile', sa.JSON(), nullable=True))
    share_cols = {c['name'] for c in inspector.get_columns('clip_shares')}
    if 'monitor_id' not in share_cols:
        with op.batch_alter_table('clip_shares') as b:
            b.add_column(sa.Column('monitor_id', sa.Integer(), nullable=True))
            b.create_index('ix_clip_shares_monitor_id', ['monitor_id'])
    if 'channel_monitors' not in tables:
        op.create_table(
            'channel_monitors',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('streamer_id', sa.Integer(), sa.ForeignKey('streamers.id', ondelete='CASCADE'), nullable=False),
            sa.Column('preset', sa.String(20), nullable=False, server_default='balanced'),
            sa.Column('paused_reason', sa.String(120), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('removed_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_channel_monitors_streamer', 'channel_monitors', ['streamer_id', 'removed_at'])
        op.create_index('uq_channel_monitors_active', 'channel_monitors', ['user_id', 'streamer_id'], unique=True,
                        postgresql_where=sa.text('removed_at IS NULL'), sqlite_where=sa.text('removed_at IS NULL'))
    if 'monitor_sessions' not in tables:
        op.create_table(
            'monitor_sessions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('monitor_id', sa.Integer(), sa.ForeignKey('channel_monitors.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('stream_sessions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('state', sa.String(16), nullable=False, server_default='watching'),
            sa.Column('from_t', sa.Float(), nullable=False, server_default='0'),
            sa.Column('until_t', sa.Float(), nullable=True),
            sa.Column('stop_reason', sa.String(120), nullable=False, server_default=''),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('stopped_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('monitor_id', 'session_id', name='uq_monitor_session'),
        )
        op.create_index('ix_monitor_sessions_session', 'monitor_sessions', ['session_id', 'state'])
        op.create_index('ix_monitor_sessions_user', 'monitor_sessions', ['user_id', 'state'])

    # One live session per channel. Created only when the data already respects it (nothing is rewritten);
    # otherwise the code-level lock still applies and the operator ends the duplicates before re-running.
    indexes = {i['name'] for i in inspector.get_indexes('stream_sessions')}
    if 'uq_stream_sessions_one_live' not in indexes:
        dup = bind.execute(sa.text("SELECT streamer_id FROM stream_sessions WHERE status = 'live' "
                                   "GROUP BY streamer_id HAVING COUNT(*) > 1")).first()
        if dup is None:
            op.create_index('uq_stream_sessions_one_live', 'stream_sessions', ['streamer_id'], unique=True,
                            postgresql_where=sa.text("status = 'live'"), sqlite_where=sa.text("status = 'live'"))
        else:
            print("0018: more than one live session for a channel; unique live index NOT created (end the duplicates first)")

    # Channels owned by a customer (PR #23) become that customer's monitors
    owned = bind.execute(sa.text(
        'SELECT s.id, s.owner_id, s.created_at FROM streamers s WHERE s.owner_id IS NOT NULL '
        'AND NOT EXISTS (SELECT 1 FROM channel_monitors m WHERE m.streamer_id = s.id AND m.user_id = s.owner_id '
        'AND m.removed_at IS NULL)')).fetchall()
    for sid, uid, created in owned:
        bind.execute(sa.text("INSERT INTO channel_monitors (user_id, streamer_id, preset, paused_reason, created_at) "
                             "VALUES (:u, :s, 'balanced', '', :c)"), {'u': uid, 's': sid, 'c': created})
        bind.execute(sa.text('UPDATE streamers SET hts_monitoring = :f WHERE id = :s'), {'f': False, 's': sid})
        bind.execute(sa.text('UPDATE streamer_grants SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = :u '
                             'AND streamer_id = :s AND created_by = :u AND revoked_at IS NULL'), {'u': uid, 's': sid})


def downgrade() -> None:
    if 'uq_stream_sessions_one_live' in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('stream_sessions')}:
        op.drop_index('uq_stream_sessions_one_live', table_name='stream_sessions')
    op.drop_index('ix_monitor_sessions_user', table_name='monitor_sessions')
    op.drop_index('ix_monitor_sessions_session', table_name='monitor_sessions')
    op.drop_table('monitor_sessions')
    op.drop_index('uq_channel_monitors_active', table_name='channel_monitors')
    op.drop_index('ix_channel_monitors_streamer', table_name='channel_monitors')
    op.drop_table('channel_monitors')
    with op.batch_alter_table('clip_shares') as b:
        b.drop_index('ix_clip_shares_monitor_id')
        b.drop_column('monitor_id')
    with op.batch_alter_table('stream_sessions') as b:
        b.drop_column('base_profile')
    with op.batch_alter_table('streamers') as b:
        b.drop_column('hts_monitoring')
