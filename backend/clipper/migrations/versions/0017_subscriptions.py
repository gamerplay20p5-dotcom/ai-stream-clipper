"""customer subscriptions: channel ownership, entitlement snapshots, idempotent billing inbox and usage metering

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-21

* ``streamers.owner_id``: NULL keeps the current behaviour (HTS channels, no quotas). A customer's channels
  point to the customer account; deleting an account with channels is refused (RESTRICT).
* ``account_subscriptions``: the latest entitlement snapshot received from the commercial service per
  (provider, subscription_ref); ``last_seq`` discards out-of-order deliveries.
* ``billing_events``: every delivered event id once (at-least-once delivery becomes exactly-once effect).
* ``usage_entries``: metered consumption with a unique (metric, source_key), so a segment or an export is
  never counted twice; totals are sums per (user, metric, period).

Additive only. Rollback: the downgrade drops the new tables and the column.
"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c['name'] for c in inspector.get_columns('streamers')}
    if 'owner_id' not in cols:
        with op.batch_alter_table('streamers') as b:
            b.add_column(sa.Column('owner_id', sa.Integer(), nullable=True))
            b.create_foreign_key('fk_streamers_owner', 'users', ['owner_id'], ['id'], ondelete='RESTRICT')
            b.create_index('ix_streamers_owner_id', ['owner_id'])
    tables = set(inspector.get_table_names())
    if 'account_subscriptions' not in tables:
        op.create_table(
            'account_subscriptions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('provider', sa.String(20), nullable=False),
            sa.Column('subscription_ref', sa.String(120), nullable=False),
            sa.Column('plan', sa.String(20), nullable=False),
            sa.Column('market', sa.String(4), nullable=False, server_default=''),
            sa.Column('financial_status', sa.String(30), nullable=False, server_default=''),
            sa.Column('review', sa.String(40), nullable=False, server_default=''),
            sa.Column('access_status', sa.String(20), nullable=False, server_default='none'),
            sa.Column('access_until', sa.DateTime(), nullable=True),
            sa.Column('period_start', sa.DateTime(), nullable=True),
            sa.Column('period_end', sa.DateTime(), nullable=True),
            sa.Column('renews_at', sa.DateTime(), nullable=True),
            sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('limits', sa.JSON(), nullable=False),
            sa.Column('manage', sa.String(30), nullable=False, server_default=''),
            sa.Column('renewal', sa.JSON(), nullable=True),
            sa.Column('payments', sa.JSON(), nullable=False),
            sa.Column('last_seq', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('provider', 'subscription_ref', name='uq_account_sub_ref'),
        )
        op.create_index('ix_account_subscriptions_user', 'account_subscriptions', ['user_id', 'access_status'])
    if 'billing_events' not in tables:
        op.create_table(
            'billing_events',
            sa.Column('event_id', sa.String(64), primary_key=True),
            sa.Column('provider', sa.String(20), nullable=False),
            sa.Column('subscription_ref', sa.String(120), nullable=False),
            sa.Column('seq', sa.BigInteger(), nullable=False),
            sa.Column('outcome', sa.String(20), nullable=False),
            sa.Column('received_at', sa.DateTime(), nullable=False),
        )
    if 'usage_entries' not in tables:
        op.create_table(
            'usage_entries',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('metric', sa.String(20), nullable=False),
            sa.Column('source_key', sa.String(160), nullable=False),
            sa.Column('period_key', sa.String(40), nullable=False),
            sa.Column('amount', sa.BigInteger(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('metric', 'source_key', name='uq_usage_metric_source'),
        )
        op.create_index('ix_usage_user_period', 'usage_entries', ['user_id', 'metric', 'period_key'])


def downgrade() -> None:
    op.drop_index('ix_usage_user_period', table_name='usage_entries')
    op.drop_table('usage_entries')
    op.drop_table('billing_events')
    op.drop_index('ix_account_subscriptions_user', table_name='account_subscriptions')
    op.drop_table('account_subscriptions')
    with op.batch_alter_table('streamers') as b:
        b.drop_index('ix_streamers_owner_id')
        b.drop_constraint('fk_streamers_owner', type_='foreignkey')
        b.drop_column('owner_id')
