"""Add cost basis tracking and transaction source fields

Revision ID: add_cost_basis_tracking
Revises: add_accounts_table
Create Date: 2026-02-26 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_cost_basis_tracking'
down_revision = 'add_accounts_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to holdings table
    op.add_column('holdings', sa.Column('cost_basis_source', sa.String(length=32), server_default='INCOMPLETE', nullable=True))
    op.add_column('holdings', sa.Column('import_date', sa.Date(), nullable=True))
    op.add_column('holdings', sa.Column('has_complete_history', sa.Boolean(), server_default=sa.false(), nullable=True))
    
    # Add columns to transactions table
    op.add_column('transactions', sa.Column('commission', sa.Numeric(), server_default='0', nullable=True))
    op.add_column('transactions', sa.Column('fees', sa.Numeric(), server_default='0', nullable=True))
    op.add_column('transactions', sa.Column('broker_transaction_id', sa.String(length=256), nullable=True))
    op.add_column('transactions', sa.Column('is_estimated', sa.Boolean(), server_default=sa.false(), nullable=True))
    op.add_column('transactions', sa.Column('source', sa.String(length=32), server_default='BROKER_CSV', nullable=True))
    
    # Add index on broker_transaction_id for fast duplicate detection
    op.create_index('ix_transactions_broker_id', 'transactions', ['broker_transaction_id'])


def downgrade() -> None:
    # Remove index
    op.drop_index('ix_transactions_broker_id', table_name='transactions')
    
    # Remove columns from transactions table
    op.drop_column('transactions', 'source')
    op.drop_column('transactions', 'is_estimated')
    op.drop_column('transactions', 'broker_transaction_id')
    op.drop_column('transactions', 'fees')
    op.drop_column('transactions', 'commission')
    
    # Remove columns from holdings table
    op.drop_column('holdings', 'has_complete_history')
    op.drop_column('holdings', 'import_date')
    op.drop_column('holdings', 'cost_basis_source')
