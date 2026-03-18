"""Add stock_splits table

Revision ID: add_stock_splits_table
Revises: redesign_portfolio_metrics
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_stock_splits_table'
down_revision = 'redesign_portfolio_metrics'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stock_splits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stock_id', sa.Integer(), sa.ForeignKey('stocks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('ratio', sa.Numeric(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=True, server_default='YFINANCE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_id', 'date', name='uq_stock_split_stock_date'),
    )
    op.create_index('ix_stock_splits_stock_id_date', 'stock_splits', ['stock_id', 'date'])


def downgrade():
    op.drop_index('ix_stock_splits_stock_id_date', table_name='stock_splits')
    op.drop_table('stock_splits')
