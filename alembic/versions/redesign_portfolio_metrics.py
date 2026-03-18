"""Redesign portfolio_metrics: add account_id FK, per-account unique indexes, new metric columns

Revision ID: redesign_portfolio_metrics
Revises: div_date_to_provider_date
Create Date: 2026-03-17

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'redesign_portfolio_metrics'
down_revision = 'div_date_to_provider_date'
branch_labels = None
depends_on = None


def upgrade():
    # Drop old single-column unique constraint
    op.drop_constraint('uq_portfolio_metrics_date', 'portfolio_metrics', type_='unique')

    # Add account_id FK column (nullable; NULL = all-accounts aggregate row)
    op.add_column('portfolio_metrics',
        sa.Column('account_id', sa.Integer(),
                  sa.ForeignKey('accounts.id', ondelete='CASCADE'),
                  nullable=True))

    # Add new metric columns
    op.add_column('portfolio_metrics', sa.Column('total_cost_basis', sa.Numeric(), nullable=True))
    op.add_column('portfolio_metrics', sa.Column('total_unrealized_gain_loss', sa.Numeric(), nullable=True))
    op.add_column('portfolio_metrics', sa.Column('dividend_yield', sa.Numeric(), nullable=True))

    # Rename total_dividends to be explicit (no-op rename for clarity in new code; column keeps same name)

    # Create partial unique index for per-account rows
    op.create_index(
        'uq_portfolio_metrics_account_date',
        'portfolio_metrics', ['account_id', 'date'],
        unique=True,
        postgresql_where=sa.text('account_id IS NOT NULL')
    )

    # Create partial unique index for aggregate rows (account_id IS NULL)
    op.create_index(
        'uq_portfolio_metrics_agg_date',
        'portfolio_metrics', ['date'],
        unique=True,
        postgresql_where=sa.text('account_id IS NULL')
    )


def downgrade():
    op.drop_index('uq_portfolio_metrics_agg_date', table_name='portfolio_metrics')
    op.drop_index('uq_portfolio_metrics_account_date', table_name='portfolio_metrics')

    op.drop_column('portfolio_metrics', 'dividend_yield')
    op.drop_column('portfolio_metrics', 'total_unrealized_gain_loss')
    op.drop_column('portfolio_metrics', 'total_cost_basis')
    op.drop_column('portfolio_metrics', 'account_id')

    op.create_unique_constraint('uq_portfolio_metrics_date', 'portfolio_metrics', ['date'])
