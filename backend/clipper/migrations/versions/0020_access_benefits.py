"""HTS access benefits: access credits (compensation days) and temporary upgrades, separate from payments

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-21

* ``access_benefits``: one row per benefit (``credit``: days added after the paid access; ``upgrade``: a higher plan's
  limits for a while). ``request_key`` is unique, so a repeated click never creates a second benefit. Revoked rows
  are kept (history).
* ``account_subscriptions.last_entitled_until`` / ``last_period_anchor``: the last access date (and the anchor of that
  period) received while the subscription was entitled, so a credit can still run after a natural end without
  resetting the period's quotas. Filled for existing entitled rows.

Promotion codes are NOT here: they are money and live in the commercial service (``commercial/server/promotions.js``).
Additive only. The downgrade drops the table and the column (benefits given until then are lost; payments untouched).
"""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c['name'] for c in inspector.get_columns('account_subscriptions')}
    with op.batch_alter_table('account_subscriptions') as b:
        for name in ('last_entitled_until', 'last_period_anchor'):
            if name not in cols:
                b.add_column(sa.Column(name, sa.DateTime(), nullable=True))
    op.execute("UPDATE account_subscriptions SET last_entitled_until = access_until, "
               "last_period_anchor = COALESCE(period_start, period_end, access_until) "
               "WHERE access_status IN ('active', 'grace') AND last_entitled_until IS NULL")
    if 'access_benefits' not in set(inspector.get_table_names()):
        op.create_table(
            'access_benefits',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('kind', sa.String(10), nullable=False),
            sa.Column('days', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('plan', sa.String(20), nullable=False, server_default=''),
            sa.Column('starts_at', sa.DateTime(), nullable=False),
            sa.Column('ends_at', sa.DateTime(), nullable=True),
            sa.Column('reason', sa.String(300), nullable=False),
            sa.Column('request_key', sa.String(64), nullable=False),
            sa.Column('created_by', sa.String(255), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('revoked_by', sa.String(255), nullable=False, server_default=''),
            sa.Column('revoke_reason', sa.String(300), nullable=False, server_default=''),
            sa.CheckConstraint("kind IN ('credit', 'upgrade')", name='ck_access_benefits_kind'),
            sa.CheckConstraint("days >= 0 AND days <= 365", name='ck_access_benefits_days'),
            sa.UniqueConstraint('request_key', name='uq_access_benefits_request_key'),
        )
        op.create_index('ix_access_benefits_user', 'access_benefits', ['user_id', 'kind'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'access_benefits' in set(inspector.get_table_names()):
        op.drop_index('ix_access_benefits_user', table_name='access_benefits')
        op.drop_table('access_benefits')
    cols = {c['name'] for c in inspector.get_columns('account_subscriptions')}
    with op.batch_alter_table('account_subscriptions') as b:
        for name in ('last_period_anchor', 'last_entitled_until'):
            if name in cols:
                b.drop_column(name)
