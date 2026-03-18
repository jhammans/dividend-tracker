"""Rename dividends.date to dividends.provider_date

Revision ID: div_date_to_provider_date
Revises: add_cost_basis_tracking
Create Date: 2026-03-17

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'div_date_to_provider_date'
down_revision = 'add_cost_basis_tracking'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('dividends', 'date', new_column_name='provider_date')


def downgrade():
    op.alter_column('dividends', 'provider_date', new_column_name='date')
