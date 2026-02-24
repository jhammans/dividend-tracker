"""add asset_type column to stocks

Revision ID: add_asset_type_to_stocks
Revises: cb35a59b646f
Create Date: 2026-02-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_asset_type_to_stocks'
down_revision = 'cb35a59b646f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'stocks',
        sa.Column('asset_type', sa.String(length=32), server_default='EQUITY', nullable=False)
    )


def downgrade():
    op.drop_column('stocks', 'asset_type')
