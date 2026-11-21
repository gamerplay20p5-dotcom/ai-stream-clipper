"""customer publishing: multi-tenant destinations, rules and publications, daily allowance and courtesy plans

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-21

* ``owner_id`` on ``oauth_states``, ``publish_accounts``, ``publish_rules`` and ``publications``: NULL keeps every
  existing row as an HTS resource (behaviour unchanged); set = owned by one customer account.
* ``publish_accounts.paused_reason`` (plan limit or HTS block), ``publish_rules.rights_confirmed_at`` /
  ``rights_note`` (the customer's statement that it may republish that channel).
* ``customer_publish_settings``: automatic publishing switch (off by default), timezone, explicit authorization,
  HTS block.
* ``publish_units``: daily allowance, one unit per publication (never counted twice).
* ``courtesy_grants``: plans given by the HTS, separate from payments.

Additive only; the downgrade removes what this revision added.
"""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

OWNED = ('oauth_states', 'publish_accounts', 'publish_rules', 'publications')


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table in OWNED:
        cols = {c['name'] for c in inspector.get_columns(table)}
        with op.batch_alter_table(table) as b:
            if 'owner_id' not in cols:
                b.add_column(sa.Column('owner_id', sa.Integer(), nullable=True))
                b.create_foreign_key(f'fk_{table}_owner', 'users', ['owner_id'], ['id'], ondelete='CASCADE')
                b.create_index(f'ix_{table}_owner_id', ['owner_id'])
            if table == 'publish_accounts' and 'paused_reason' not in cols:
                b.add_column(sa.Column('paused_reason', sa.String(120), nullable=False, server_default=''))
            if table == 'publish_rules' and 'rights_confirmed_at' not in cols:
                b.add_column(sa.Column('rights_confirmed_at', sa.DateTime(), nullable=True))
                b.add_column(sa.Column('rights_note', sa.String(500), nullable=False, server_default=''))
    if 'customer_publish_settings' not in tables:
        op.create_table(
            'customer_publish_settings',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('timezone', sa.String(64), nullable=False, server_default='America/Sao_Paulo'),
            sa.Column('terms_accepted_at', sa.DateTime(), nullable=True),
            sa.Column('blocked_reason', sa.String(200), nullable=False, server_default=''),
            sa.Column('blocked_by', sa.String(255), nullable=False, server_default=''),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
    if 'publish_units' not in tables:
        op.create_table(
            'publish_units',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('publication_id', sa.Integer(), sa.ForeignKey('publications.id', ondelete='CASCADE'), nullable=False),
            sa.Column('day', sa.String(10), nullable=False),
            sa.Column('state', sa.String(10), nullable=False, server_default='reserved'),
            sa.Column('reason', sa.String(200), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('publication_id', name='uq_publish_unit_publication'),
        )
        op.create_index('ix_publish_units_user_day', 'publish_units', ['user_id', 'day', 'state'])
    if 'courtesy_grants' not in tables:
        op.create_table(
            'courtesy_grants',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('account_subscriptions.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('plan', sa.String(20), nullable=False),
            sa.Column('starts_at', sa.DateTime(), nullable=False),
            sa.Column('ends_at', sa.DateTime(), nullable=False),
            sa.Column('reason', sa.String(300), nullable=False),
            sa.Column('created_by', sa.String(255), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('revoked_by', sa.String(255), nullable=False, server_default=''),
            sa.Column('revoke_reason', sa.String(300), nullable=False, server_default=''),
        )
        op.create_index('ix_courtesy_grants_user_id', 'courtesy_grants', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_courtesy_grants_user_id', table_name='courtesy_grants')
    op.drop_table('courtesy_grants')
    op.drop_index('ix_publish_units_user_day', table_name='publish_units')
    op.drop_table('publish_units')
    op.drop_table('customer_publish_settings')
    for table in reversed(OWNED):
        with op.batch_alter_table(table) as b:
            if table == 'publish_rules':
                b.drop_column('rights_note')
                b.drop_column('rights_confirmed_at')
            if table == 'publish_accounts':
                b.drop_column('paused_reason')
            b.drop_index(f'ix_{table}_owner_id')
            b.drop_constraint(f'fk_{table}_owner', type_='foreignkey')
            b.drop_column('owner_id')
