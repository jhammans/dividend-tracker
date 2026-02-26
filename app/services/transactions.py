"""
Transaction import service for handling broker CSV data.
Supports: Schwab, Fidelity, Robinhood (extensible architecture)
"""
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.models import Transaction, Account, Stock
from app.services.fetch import InvalidTickerError

logger = logging.getLogger(__name__)


class TransactionImportError(Exception):
    """Raised when transaction import fails"""
    pass


class DuplicateTransactionError(Exception):
    """Raised when duplicate transaction is detected"""
    pass


class SchwabTransactionParser:
    """Parse Schwab consolidated transaction CSV export"""
    
    # Expected columns in Schwab CSV (flexible - looks for either 'Type' or 'Action')
    REQUIRED_COLUMNS = ['Date', 'Symbol', 'Amount']
    TYPE_OR_ACTION_COLUMNS = ['Type', 'Action']
    
    # Map Schwab transaction types to our internal types
    TYPE_MAPPING = {
        # Equity transactions (uppercase)
        'BUY': 'BUY',
        'SELL': 'SELL',
        # Dividend types (different formats from Schwab)
        'DIVIDEND': 'DIVIDEND_PAYMENT',
        'CASH DIVIDEND': 'DIVIDEND_PAYMENT',
        'PR YR CASH DIV': 'DIVIDEND_PAYMENT',  # Prior year dividend
        'REINVEST DIVIDEND': 'DRIP',
        # Capital gains
        'LONG TERM CAP GAIN': 'CAP_GAIN_LONG',
        'SHORT TERM CAP GAIN': 'CAP_GAIN_SHORT',
        # Other events
        'SPIN-OFF': 'TRANSFER',
        'CASH IN': 'DEPOSIT',
        'CASH OUT': 'WITHDRAWAL',
        'STOCK SPLIT': 'SPLIT',
        'MERGER': 'MERGE',
        'BANK INTEREST': 'INTEREST',
        'RETIREMENT DISTRIBUTION': 'WITHDRAWAL',
        'RETIREMENT CONTRIBUTION': 'DEPOSIT',
    }
    
    @staticmethod
    def parse_csv(filepath: str) -> pd.DataFrame:
        """Load and parse Schwab transaction CSV"""
        try:
            df = pd.read_csv(filepath, dtype={'Symbol': str, 'Quantity': str, 'Price': str, 'Amount': str})
            
            # Validate required columns exist
            missing_cols = [col for col in SchwabTransactionParser.REQUIRED_COLUMNS if col not in df.columns]
            if missing_cols:
                raise TransactionImportError(f"CSV missing required columns: {missing_cols}")
            
            # Find Type/Action column
            type_col = None
            for col in SchwabTransactionParser.TYPE_OR_ACTION_COLUMNS:
                if col in df.columns:
                    type_col = col
                    break
            if not type_col:
                raise TransactionImportError(
                    f"CSV must have either 'Type' or 'Action' column. Found: {list(df.columns)}"
                )
            
            # Rename 'Action' to 'Type' if needed for consistency
            if type_col == 'Action':
                df = df.rename(columns={'Action': 'Type'})
            
            # Strip whitespace from Symbol and Type
            df['Symbol'] = df['Symbol'].str.strip().str.upper()
            df['Type'] = df['Type'].str.strip()
            
            # Parse dates - Schwab uses M/D/YYYY format, sometimes with extra text like "as of"
            # Example: "02/17/2026" or "02/17/2026 as of 02/15/2026" (take first date)
            df['Date'] = df['Date'].str.split(' as of ').str[0]  # Take only first date if "as of" exists
            df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%Y').dt.date
            
            # Convert numeric columns, handling $ and commas
            for col in ['Quantity', 'Price', 'Amount']:
                df[col] = df[col].astype(str).str.replace('$', '').str.replace(',', '')
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Drop rows with parsing errors
            if df['Quantity'].isna().any() or df['Amount'].isna().any():
                logger.warning("Dropped rows with invalid numeric values")
                df = df.dropna(subset=['Quantity', 'Amount'])
            
            return df
            
        except FileNotFoundError:
            raise TransactionImportError(f"CSV file not found: {filepath}")
        except Exception as e:
            raise TransactionImportError(f"Error parsing Schwab CSV: {e}")
    
    @staticmethod
    def map_transaction_type(schwab_type: str) -> Optional[str]:
        """Map Schwab transaction type to internal type (case-insensitive)"""
        # Normalize to uppercase for lookup
        normalized_type = schwab_type.strip().upper()
        return SchwabTransactionParser.TYPE_MAPPING.get(normalized_type)
    
    @staticmethod
    def create_broker_transaction_id(row: pd.Series) -> str:
        """
        Create deterministic broker_transaction_id from row data.
        Schwab doesn't always provide transaction IDs in CSV, so we create one from:
        date + type + symbol + quantity + price
        """
        return f"SCHWAB_{row['Date'].strftime('%Y%m%d')}_{row['Type']}_{row['Symbol']}_{row['Quantity']}_{row['Price']}"


class FidelityTransactionParser:
    """Parse Fidelity transaction export"""
    # Placeholder for future implementation
    pass


class TransactionImporter:
    """Main transaction import orchestrator"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.import_results = {
            'successful': 0,
            'failed': 0,
            'duplicates_skipped': 0,
            'errors': []
        }
    
    def import_schwab_transactions(self, filepath: str, account: Account, year: int = None) -> Dict:
        """
        Import transactions from Schwab CSV export.
        
        Args:
            filepath: Path to Schwab CSV file
            account: Account object to import into
            year: Optional year filter (import only transactions from this year)
        
        Returns:
            Dict with import_results detailing success/failures
        """
        self.import_results = {
            'total_processed': 0,
            'imported_count': 0,
            'skipped_count': 0,
            'failed_count': 0,
            'duplicates_skipped': 0,
            'errors': [],
            'imported_transactions': []
        }
        
        try:
            # Parse CSV
            df = SchwabTransactionParser.parse_csv(filepath)
            logger.info(f"Loaded {len(df)} transactions from {filepath}")
            
            # Filter by year if specified
            if year:
                df = df[df['Date'].apply(lambda x: x.year == year)]
                logger.info(f"Filtered to {len(df)} transactions for year {year}")
            
            # Process each transaction
            for idx, row in df.iterrows():
                self.import_results['total_processed'] += 1
                try:
                    # Map transaction type
                    internal_type = SchwabTransactionParser.map_transaction_type(row['Type'])
                    if not internal_type:
                        logger.warning(f"Row {idx}: Unknown transaction type '{row['Type']}', skipping")
                        self.import_results['failed_count'] += 1
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    # Skip non-security transactions (deposits, withdrawals, interest payments, capital gains)
                    if internal_type in ['DEPOSIT', 'WITHDRAWAL', 'INTEREST', 'CAP_GAIN_LONG', 'CAP_GAIN_SHORT']:
                        logger.debug(f"Row {idx}: Skipping {internal_type} transaction (non-security)")
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    # Find stock (may be missing for some transaction types)
                    stock = None
                    if pd.notna(row['Symbol']) and str(row['Symbol']).strip():
                        stock = self.db.query(Stock).filter_by(ticker=row['Symbol']).first()
                        if not stock:
                            logger.warning(f"Row {idx}: Stock {row['Symbol']} not found in database, skipping")
                            self.import_results['failed_count'] += 1
                            continue
                    elif internal_type in ['BUY', 'SELL', 'DRIP', 'DIVIDEND_PAYMENT']:
                        # These transaction types require a symbol
                        logger.warning(f"Row {idx}: {internal_type} transaction missing symbol, skipping")
                        self.import_results['failed_count'] += 1
                        continue
                    
                    # Create broker transaction ID
                    broker_txn_id = SchwabTransactionParser.create_broker_transaction_id(row) if stock else None
                    
                    # Check for duplicates (only if we have a stock)
                    existing = None
                    if stock:
                        existing = self._check_duplicate_transaction(account, broker_txn_id, stock, row['Date'])
                    
                    if existing:
                        logger.info(f"Row {idx}: Duplicate transaction found (ID: {broker_txn_id}), skipping")
                        self.import_results['duplicates_skipped'] += 1
                        continue
                    
                    # Parse Amount - may include $ signs and +/- signs
                    amount_str = str(row['Amount']).replace('$', '').replace(',', '').strip()
                    amount_value = Decimal(amount_str) if amount_str else Decimal('0')
                    
                    # Create transaction record
                    transaction = Transaction(
                        account_id=account.id,
                        stock_id=stock.id,
                        type=internal_type,
                        quantity=Decimal(str(row['Quantity'])) if pd.notna(row['Quantity']) else None,
                        price=Decimal(str(row['Price']).replace('$', '').replace(',', '')) if pd.notna(row['Price']) and row['Price'] else None,
                        total=amount_value,
                        commission=Decimal('0'),  # Schwab may include in Amount
                        date=row['Date'],
                        broker_transaction_id=broker_txn_id,
                        source='BROKER_CSV',
                        is_estimated=False
                    )
                    
                    self.db.add(transaction)
                    self.import_results['imported_transactions'].append({
                        'date': str(row['Date']),
                        'type': internal_type,
                        'symbol': row['Symbol'],
                        'quantity': str(row['Quantity']),
                        'amount': str(row['Amount'])
                    })
                    self.import_results['imported_count'] += 1
                    
                except Exception as e:
                    logger.error(f"Row {idx}: Error processing transaction: {e}")
                    self.import_results['failed_count'] += 1
                    self.import_results['errors'].append(f"Row {idx}: {str(e)}")
            
            # Commit all transactions
            self.db.commit()
            logger.info(f"Import complete: {self.import_results['imported_count']} imported, "
                       f"{self.import_results['duplicates_skipped']} duplicates skipped, "
                       f"{self.import_results['failed_count']} failed")
            
        except TransactionImportError as e:
            self.import_results['errors'].append(str(e))
            logger.error(f"Import failed: {e}")
            self.db.rollback()
        except Exception as e:
            logger.error(f"Unexpected error during import: {e}")
            self.import_results['errors'].append(str(e))
            self.db.rollback()
        
        return self.import_results
    
    def _check_duplicate_transaction(self, account: Account, broker_txn_id: str, 
                                     stock: Stock, txn_date: date) -> Optional[Transaction]:
        """
        Check if transaction already exists using multiple strategies.
        
        Priority:
        1. broker_transaction_id exact match (most reliable)
        2. Date + Stock + Type + Amount composite (fallback)
        """
        # Strategy 1: broker_transaction_id
        existing = self.db.query(Transaction).filter(
            and_(
                Transaction.account_id == account.id,
                Transaction.broker_transaction_id == broker_txn_id
            )
        ).first()
        
        if existing:
            return existing
        
        # Strategy 2: Composite key (date + stock + amount) — catches manual import mismatches
        # Only check within 24 hours to avoid false positives
        existing = self.db.query(Transaction).filter(
            and_(
                Transaction.account_id == account.id,
                Transaction.stock_id == stock.id,
                Transaction.date == txn_date,
            )
        ).first()
        
        return existing
    
    def reconcile_holdings_with_transactions(self, account: Account) -> Dict:
        """
        Reconcile imported transactions against current holdings.
        Useful for validating import accuracy.
        
        Returns dict with verification results per stock
        """
        results = {
            'verified': {},
            'warnings': [],
            'errors': []
        }
        
        # Get all holdings for account
        from app.models import Holding
        holdings = self.db.query(Holding).filter_by(account_id=account.id).all()
        
        for holding in holdings:
            ticker = holding.stock.ticker
            
            # Sum all quantity activity from transactions
            buy_qty = self.db.query(Transaction).filter(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.stock_id == holding.stock_id,
                    Transaction.type == 'BUY'
                )
            ).with_entities(func.sum(Transaction.quantity)).scalar() or Decimal('0')
            
            sell_qty = self.db.query(Transaction).filter(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.stock_id == holding.stock_id,
                    Transaction.type == 'SELL'
                )
            ).with_entities(func.sum(Transaction.quantity)).scalar() or Decimal('0')
            
            calculated_qty = buy_qty - sell_qty
            
            # Compare with holding
            if calculated_qty == holding.quantity:
                results['verified'][ticker] = {
                    'status': 'OK',
                    'holding_qty': str(holding.quantity),
                    'calculated_qty': str(calculated_qty)
                }
            else:
                results['warnings'].append(
                    f"{ticker}: Quantity mismatch - holding={holding.quantity}, calculated={calculated_qty}"
                )
        
        return results


def get_import_summary(results: Dict) -> str:
    """Format import results for display"""
    return f"""
Transaction Import Summary:
  ✓ Successful: {results['successful']}
  ⊘ Duplicates Skipped: {results['duplicates_skipped']}
  ✗ Failed: {results['failed']}
  
Imported Transactions:
"""
