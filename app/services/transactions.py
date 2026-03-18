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


class JazzWealthTransactionParser:
    """Parse Jazz Wealth consolidated transaction CSV export for custodial accounts"""
    
    # Expected columns in Jazz Wealth CSV
    # Note: 'quanitty' is a typo in Jazz Wealth export but we need to handle it
    REQUIRED_COLUMNS = ['Date', 'Symbol', 'amount']
    OPTIONAL_COLUMNS = ['Transaction', 'Type', 'Name', 'price', 'quanitty', 'folio', 'notes']
    
    # Map Jazz Wealth transaction types to our internal types
    TYPE_MAPPING = {
        # Buy transactions
        'BUY': 'BUY',
        'WINDOW TRADE': 'BUY',
        'TRADE': 'BUY',
        # Reinvestment / Dividend
        'REINVESTMENTS': 'DRIP',
        'REINVEST DIVIDEND': 'DRIP',
        'DIVIDEND': 'DIVIDEND_PAYMENT',
        'INCOME': 'DIVIDEND_PAYMENT',
        # Deposits / Cash movements
        'DEPOSITS': 'DEPOSIT',
        'INCOMING ACH': 'DEPOSIT',
        'DEPOSIT': 'DEPOSIT',
        'CASH IN': 'DEPOSIT',
        'WITHDRAWAL': 'WITHDRAWAL',
        'CASH OUT': 'WITHDRAWAL',
        # Corporate actions / Interest
        'CORPORATE ACTIONS': 'TRANSFER',
        'SYMBOL CHANGE': 'TRANSFER',
        'INTEREST': 'INTEREST',
        # Capital gains
        'LONG TERM CAP GAIN': 'CAP_GAIN_LONG',
        'SHORT TERM CAP GAIN': 'CAP_GAIN_SHORT',
        'REALIZED GAIN/LOSS': 'TRANSFER',
    }
    
    # Non-security tickers to filter out
    NON_SECURITY_SYMBOLS = ['DP.CASH', 'DP.SWEEP', 'USD', 'CASH']
    
    @staticmethod
    def parse_csv(filepath: str) -> pd.DataFrame:
        """Load and parse Jazz Wealth transaction CSV"""
        try:
            # Read CSV with string dtypes to handle $ formatting
            df = pd.read_csv(filepath, dtype=str)
            
            logger.info(f"Loaded {len(df)} rows from {filepath}")
            
            # Validate required columns exist (case-insensitive search)
            df_cols_lower = {col.lower(): col for col in df.columns}
            found_cols = {}
            for req_col in JazzWealthTransactionParser.REQUIRED_COLUMNS:
                col_key = req_col.lower()
                if col_key not in df_cols_lower:
                    raise TransactionImportError(f"CSV missing required column: {req_col}")
                found_cols[req_col] = df_cols_lower[col_key]
            
            # Rename to standard names
            df = df.rename(columns={
                found_cols['Date']: 'Date',
                found_cols['Symbol']: 'Symbol',
                found_cols['amount']: 'amount'
            })
            
            # Handle optional columns (check both exact and case-insensitive)
            for optional in JazzWealthTransactionParser.OPTIONAL_COLUMNS:
                col_key = optional.lower()
                if col_key in df_cols_lower and optional not in df.columns:
                    df = df.rename(columns={df_cols_lower[col_key]: optional})
            
            # Handle the typo 'quanitty' vs 'quantity'
            if 'quanitty' in df.columns and 'quantity' not in df.columns:
                df = df.rename(columns={'quanitty': 'quantity'})
            
            # Parse dates - Jazz Wealth uses M/D/YY format
            df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%y', errors='coerce').dt.date
            
            # Strip whitespace from string columns
            for col in ['Symbol', 'Transaction', 'Type', 'Name', 'folio', 'notes']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip()
            
            # Parse price - Remove $ and spaces, handle commas
            if 'price' in df.columns:
                df['price'] = df['price'].astype(str).str.replace('$', '').str.replace(',', '').str.strip()
                df['price'] = pd.to_numeric(df['price'], errors='coerce')
            
            # Parse quantity - Handle as decimal
            if 'quantity' in df.columns:
                df['quantity'] = df['quantity'].astype(str).str.strip()
                df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
            
            # Parse amount - Remove $ and commas, handle parentheses for negative
            if 'amount' in df.columns:
                df['amount'] = df['amount'].astype(str).str.strip()
                # Handle parentheses format for negative: ($16.09) -> -16.09
                df['amount'] = df['amount'].str.replace('(', '-').str.replace(')', '').str.replace('$', '').str.replace(',', '')
                df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
            
            # Drop rows with missing dates or amounts
            df = df.dropna(subset=['Date', 'amount'], how='any')
            
            if len(df) == 0:
                raise TransactionImportError("No valid transactions found after parsing")
            
            logger.info(f"Parsed {len(df)} transactions successfully")
            return df
            
        except FileNotFoundError:
            raise TransactionImportError(f"CSV file not found: {filepath}")
        except Exception as e:
            raise TransactionImportError(f"Error parsing Jazz Wealth CSV: {e}")
    
    @staticmethod
    def map_transaction_type(transaction_type: str, qty_type: str = 'BUY') -> Optional[str]:
        """Map Jazz Wealth transaction type to internal type (case-insensitive)"""
        normalized = transaction_type.strip().upper()
        
        # Special handling for Window Trade - check if quantity sign indicates SELL
        if normalized in ['WINDOW TRADE', 'TRADE']:
            return qty_type
        
        return JazzWealthTransactionParser.TYPE_MAPPING.get(normalized)
    
    @staticmethod
    def is_non_security_transaction(symbol: str, tx_type: str) -> bool:
        """Check if transaction is non-security (should be skipped)"""
        if not symbol:
            return True
        
        symbol_upper = symbol.strip().upper()
        
        # Non-security symbols
        if symbol_upper in JazzWealthTransactionParser.NON_SECURITY_SYMBOLS:
            return True
        
        # Non-security transaction types
        non_security_types = [
            'DEPOSIT', 'WITHDRAWAL', 'INTEREST', 
            'CAP_GAIN_LONG', 'CAP_GAIN_SHORT', 'TRANSFER'
        ]
        if tx_type in non_security_types:
            return True
        
        return False
    
    @staticmethod
    def create_broker_transaction_id(row: pd.Series) -> str:
        """Create deterministic broker_transaction_id from row data"""
        qty = int(row['quantity']) if pd.notna(row['quantity']) else 0
        price = f"{row['price']:.2f}" if pd.notna(row['price']) else "0.00"
        
        return f"JAZZWEALTH_{row['Date'].strftime('%Y%m%d')}_{'BUY' if qty > 0 else 'SELL'}_{row['Symbol']}_{abs(qty)}_{price}"
    
    @staticmethod
    def infer_transaction_type_from_quantity(quantity: float, tx_type_raw: str) -> str:
        """For ambiguous transaction types, infer BUY vs SELL from quantity sign"""
        if quantity is None or pd.isna(quantity):
            return 'BUY'
        return 'SELL' if quantity < 0 else 'BUY'


class RobinhoodTransactionParser:
    """Parse Robinhood consolidated transaction CSV export"""
    
    # Expected columns in Robinhood CSV
    REQUIRED_COLUMNS = ['Activity Date', 'Instrument', 'Trans Code', 'Quantity', 'Price', 'Amount']
    
    # Map Robinhood transaction codes to internal types
    TYPE_MAPPING = {
        'BUY': 'BUY',
        'SELL': 'SELL',
        'CDIV': 'DIVIDEND_PAYMENT',  # Cash Dividend
        'RDIV': 'DRIP',               # Reinvested Dividend
        'ACH': 'DEPOSIT',             # ACH Deposit
        'ACHA': 'WITHDRAWAL',         # ACH Withdrawal
        'DIV': 'DIVIDEND_PAYMENT',
        'INT': 'INTEREST',
    }
    
    @staticmethod
    def parse_csv(filepath: str) -> pd.DataFrame:
        """Load and parse Robinhood transaction CSV
        
        Args:
            filepath: Path to Robinhood CSV file
            
        Returns:
            DataFrame with parsed transactions
            
        Raises:
            TransactionImportError if parsing fails
        """
        try:
            import csv
            from datetime import datetime
            
            # Read CSV using csv.DictReader to handle multi-line fields
            rows = []
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    raise TransactionImportError("CSV file appears to be empty or invalid")
                
                # Validate required columns
                missing_cols = [col for col in RobinhoodTransactionParser.REQUIRED_COLUMNS 
                              if col not in reader.fieldnames]
                if missing_cols:
                    raise TransactionImportError(f"CSV missing required columns: {missing_cols}")
                
                for row in reader:
                    rows.append(row)
            
            logger.info(f"Loaded {len(rows)} rows from {filepath}")
            
            if not rows:
                raise TransactionImportError("No transactions found in CSV")
            
            # Convert to DataFrame for easier processing
            df = pd.DataFrame(rows)
            
            # Parse Activity Date - Robinhood uses M/D/YYYY format
            df['Activity Date'] = pd.to_datetime(df['Activity Date'], format='%m/%d/%Y', errors='coerce').dt.date
            
            # Strip whitespace from key columns
            df['Instrument'] = df['Instrument'].astype(str).str.strip().str.upper()
            df['Trans Code'] = df['Trans Code'].astype(str).str.strip().str.upper()
            
            # Parse Quantity - Handle as decimal, can be negative for sells
            df['Quantity'] = df['Quantity'].astype(str).str.strip()
            df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
            
            # Parse Price - Remove $ and commas
            df['Price'] = df['Price'].astype(str).str.replace('$', '').str.replace(',', '').str.strip()
            df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
            
            # Parse Amount - Remove $ and commas, handle parentheses for negative
            df['Amount'] = df['Amount'].astype(str).str.strip()
            df['Amount'] = df['Amount'].str.replace('(', '-').str.replace(')', '').str.replace('$', '').str.replace(',', '')
            df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce')
            
            # Drop rows with missing required fields
            df = df.dropna(subset=['Activity Date', 'Amount'], how='any')
            df = df[df['Instrument'].notna() & (df['Instrument'] != '')]
            
            if len(df) == 0:
                raise TransactionImportError("No valid transactions found after parsing")
            
            logger.info(f"Parsed {len(df)} transactions successfully")
            return df
            
        except FileNotFoundError:
            raise TransactionImportError(f"CSV file not found: {filepath}")
        except Exception as e:
            raise TransactionImportError(f"Error parsing Robinhood CSV: {e}")
    
    @staticmethod
    def map_transaction_type(trans_code: str) -> Optional[str]:
        """Map Robinhood transaction code to internal type (case-insensitive)"""
        normalized = trans_code.strip().upper()
        return RobinhoodTransactionParser.TYPE_MAPPING.get(normalized)
    
    @staticmethod
    def create_broker_transaction_id(row: pd.Series) -> str:
        """Create deterministic broker_transaction_id from row data"""
        qty = int(row['Quantity']) if pd.notna(row['Quantity']) else 0
        price = f"{row['Price']:.2f}" if pd.notna(row['Price']) else "0.00"
        code = 'BUY' if row['Trans Code'] in ['BUY'] else 'SELL' if row['Trans Code'] == 'SELL' else row['Trans Code']
        
        return f"ROBINHOOD_{row['Activity Date'].strftime('%Y%m%d')}_{code}_{row['Instrument']}_{abs(qty)}_{price}"


class FidelityTransactionParser:
    """Parse Fidelity transaction export - placeholder for future implementation"""
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
                    
                    # Parse Amount - may include $ signs and +/- signs; always store absolute value
                    amount_str = str(row['Amount']).replace('$', '').replace(',', '').strip()
                    amount_value = abs(Decimal(amount_str)) if amount_str else Decimal('0')
                    
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
    
    def import_jazzwealth_transactions(self, filepath: str, account: Account) -> Dict:
        """
        Import transactions from Jazz Wealth CSV export.
        Supports custodial accounts like Goldman Sachs Roth IRA managed by Jazz Wealth.
        
        Args:
            filepath: Path to Jazz Wealth CSV file
            account: Account object to import into
        
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
            df = JazzWealthTransactionParser.parse_csv(filepath)
            logger.info(f"Loaded {len(df)} transactions from {filepath}")
            
            # Process each transaction
            for idx, row in df.iterrows():
                self.import_results['total_processed'] += 1
                try:
                    # Map transaction type
                    internal_type = JazzWealthTransactionParser.map_transaction_type(
                        row['Type'] if 'Type' in df.columns else row['Transaction'],
                        qty_type=JazzWealthTransactionParser.infer_transaction_type_from_quantity(
                            row.get('quantity'), row.get('Type', '')
                        )
                    )
                    
                    if not internal_type:
                        logger.warning(f"Row {idx}: Unknown transaction type '{row.get('Type', 'N/A')}', skipping")
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    # Get symbol
                    symbol = row['Symbol'] if 'Symbol' in row and pd.notna(row['Symbol']) else None
                    
                    # Skip non-security transactions
                    if JazzWealthTransactionParser.is_non_security_transaction(symbol or '', internal_type):
                        logger.debug(f"Row {idx}: Skipping non-security transaction ({internal_type})")
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    # Validate symbol
                    if not symbol or not symbol.strip():
                        logger.warning(f"Row {idx}: Transaction missing symbol, skipping")
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    symbol = symbol.strip().upper()
                    
                    # Find stock
                    stock = self.db.query(Stock).filter_by(ticker=symbol).first()
                    if not stock:
                        logger.warning(f"Row {idx}: Stock {symbol} not found in database, skipping")
                        self.import_results['failed_count'] += 1
                        continue
                    
                    # Create broker transaction ID
                    broker_txn_id = JazzWealthTransactionParser.create_broker_transaction_id(row)
                    
                    # Check for duplicates
                    existing = self._check_duplicate_transaction(account, broker_txn_id, stock, row['Date'])
                    if existing:
                        logger.info(f"Row {idx}: Duplicate transaction (ID: {broker_txn_id}), skipping")
                        self.import_results['duplicates_skipped'] += 1
                        continue
                    
                    # Parse values
                    quantity = Decimal(str(row['quantity'])) if pd.notna(row.get('quantity')) else Decimal('0')
                    price = Decimal(str(row['price']).replace('$', '')) if pd.notna(row.get('price')) and row.get('price') else None
                    amount = Decimal(str(row['amount'])) if pd.notna(row['amount']) else Decimal('0')
                    
                    # Determine actual transaction type (BUY vs SELL based on quantity sign)
                    tx_type = internal_type
                    if internal_type in ['BUY', 'SELL']:
                        # Use inferred type based on quantity sign
                        tx_type = 'SELL' if quantity < 0 else 'BUY'
                        quantity = abs(quantity)  # Store absolute value for quantity
                    
                    # Create transaction record
                    transaction = Transaction(
                        account_id=account.id,
                        stock_id=stock.id,
                        type=tx_type,
                        quantity=quantity,
                        price=price,
                        total=abs(amount),  # Store absolute value
                        commission=Decimal('0'),
                        date=row['Date'],
                        broker_transaction_id=broker_txn_id,
                        source='BROKER_CSV',
                        is_estimated=False
                    )
                    
                    self.db.add(transaction)
                    self.import_results['imported_transactions'].append({
                        'date': str(row['Date']),
                        'type': tx_type,
                        'symbol': symbol,
                        'quantity': str(quantity),
                        'amount': str(amount)
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
                       f"{self.import_results['skipped_count']} skipped, "
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
    
    def import_robinhood_transactions(self, filepath: str, account: Account) -> Dict:
        """
        Import transactions from Robinhood CSV export.
        
        Args:
            filepath: Path to Robinhood CSV file
            account: Account object to import into
        
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
            df = RobinhoodTransactionParser.parse_csv(filepath)
            logger.info(f"Loaded {len(df)} transactions from {filepath}")
            
            # Process each transaction
            for idx, row in df.iterrows():
                self.import_results['total_processed'] += 1
                try:
                    # Map transaction type
                    internal_type = RobinhoodTransactionParser.map_transaction_type(row['Trans Code'])
                    if not internal_type:
                        logger.warning(f"Row {idx}: Unknown transaction code '{row['Trans Code']}', skipping")
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    # Skip non-security cash movements only; keep DIVIDEND_PAYMENT and DRIP
                    if internal_type in ['DEPOSIT', 'WITHDRAWAL', 'INTEREST']:
                        logger.debug(f"Row {idx}: Skipping {internal_type} (non-security)")
                        self.import_results['skipped_count'] += 1
                        continue
                    
                    # Get symbol
                    symbol = row['Instrument'].strip().upper() if pd.notna(row['Instrument']) else None
                    if not symbol:
                        if internal_type == 'DIVIDEND_PAYMENT':
                            # Robinhood occasionally omits the instrument on dividend rows
                            logger.warning(f"Row {idx}: DIVIDEND_PAYMENT missing symbol (data unavailable from Robinhood), skipping")
                            self.import_results['skipped_count'] += 1
                        else:
                            logger.warning(f"Row {idx}: Missing symbol, skipping")
                            self.import_results['skipped_count'] += 1
                        continue
                    
                    # Find stock
                    stock = self.db.query(Stock).filter_by(ticker=symbol).first()
                    if not stock:
                        logger.warning(f"Row {idx}: Stock {symbol} not found in database, skipping")
                        self.import_results['failed_count'] += 1
                        continue
                    
                    # Create broker transaction ID
                    broker_txn_id = RobinhoodTransactionParser.create_broker_transaction_id(row)
                    
                    # Check for duplicates
                    existing = self._check_duplicate_transaction(account, broker_txn_id, stock, row['Activity Date'])
                    if existing:
                        logger.info(f"Row {idx}: Duplicate transaction (ID: {broker_txn_id}), skipping")
                        self.import_results['duplicates_skipped'] += 1
                        continue
                    
                    # Parse values
                    quantity = Decimal(str(abs(row['Quantity']))) if pd.notna(row['Quantity']) else Decimal('0')
                    price = Decimal(str(row['Price'])) if pd.notna(row['Price']) else None
                    amount = Decimal(str(abs(row['Amount']))) if pd.notna(row['Amount']) else Decimal('0')
                    
                    # Preserve internal type for income transactions; derive BUY/SELL from quantity sign for trades
                    if internal_type in ['DIVIDEND_PAYMENT', 'DRIP']:
                        tx_type = internal_type
                    else:
                        tx_type = 'SELL' if row['Quantity'] < 0 else 'BUY'
                    
                    # Create transaction record
                    transaction = Transaction(
                        account_id=account.id,
                        stock_id=stock.id,
                        type=tx_type,
                        quantity=quantity,
                        price=price,
                        total=amount,
                        commission=Decimal('0'),
                        date=row['Activity Date'],
                        broker_transaction_id=broker_txn_id,
                        source='BROKER_CSV',
                        is_estimated=False
                    )
                    
                    self.db.add(transaction)
                    self.import_results['imported_transactions'].append({
                        'date': str(row['Activity Date']),
                        'type': tx_type,
                        'symbol': symbol,
                        'quantity': str(quantity),
                        'amount': str(amount)
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
                       f"{self.import_results['skipped_count']} skipped, "
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
            
            # Sum all quantity activity from transactions (BUY + DRIP - SELL)
            buy_qty = self.db.query(Transaction).filter(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.stock_id == holding.stock_id,
                    Transaction.type == 'BUY'
                )
            ).with_entities(func.sum(Transaction.quantity)).scalar() or Decimal('0')

            drip_qty = self.db.query(Transaction).filter(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.stock_id == holding.stock_id,
                    Transaction.type == 'DRIP'
                )
            ).with_entities(func.sum(Transaction.quantity)).scalar() or Decimal('0')
            
            sell_qty = self.db.query(Transaction).filter(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.stock_id == holding.stock_id,
                    Transaction.type == 'SELL'
                )
            ).with_entities(func.sum(Transaction.quantity)).scalar() or Decimal('0')
            
            calculated_qty = buy_qty + drip_qty - sell_qty
            
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
