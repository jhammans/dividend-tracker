"""add accounts table and update holdings/transactions

Revision ID: add_accounts_table
Revises: add_asset_type_to_stocks
Create Date: 2026-02-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_accounts_table'
down_revision = 'add_asset_type_to_stocks'
branch_labels = None
depends_on = None


def upgrade():
    # Create accounts table
    op.create_table(
        'accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_name', sa.String(length=256), nullable=False),
        sa.Column('broker', sa.String(length=32), nullable=False),
        sa.Column('account_type', sa.String(length=32), nullable=False),
        sa.Column('account_number', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Add account_id to holdings table
    op.add_column('holdings', sa.Column('account_id', sa.Integer(), nullable=True))
    
    # Add foreign key constraint to holdings
    op.create_foreign_key(
        'fk_holdings_account_id',
        'holdings', 'accounts',
        ['account_id'], ['id'],
        ondelete='CASCADE'
    )
    
    # Drop old unique constraint on holdings
    op.drop_constraint('uq_holdings_stock', 'holdings', type_='unique')
    
    # Create new unique constraint on holdings
    op.create_unique_constraint('uq_holding_account_stock', 'holdings', ['account_id', 'stock_id'])
    
    # Add account_id to transactions table
    op.add_column('transactions', sa.Column('account_id', sa.Integer(), nullable=True))
    
    # Add foreign key constraint to transactions
    op.create_foreign_key(
        'fk_transactions_account_id',
        'transactions', 'accounts',
        ['account_id'], ['id'],
        ondelete='CASCADE'
    )


def downgrade():
    # Remove foreign key and column from transactions
    op.drop_constraint('fk_transactions_account_id', 'transactions', type_='foreignkey')
    op.drop_column('transactions', 'account_id')
    
    # Remove foreign key and constraints from holdings
    op.drop_constraint('uq_holding_account_stock', 'holdings', type_='unique')
    op.drop_constraint('fk_holdings_account_id', 'holdings', type_='foreignkey')
    op.drop_column('holdings', 'account_id')
    
    # Recreate old unique constraint on holdings
    op.create_unique_constraint('uq_holdings_stock', 'holdings', ['stock_id'])
    
    # Drop accounts table
    op.drop_table('accounts')
