"""
Holdings import service for broker position snapshots.
Loads current position data into the Holding table.
"""
import logging
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import Holding, Account, Stock

logger = logging.getLogger(__name__)


class HoldingsImportError(Exception):
    """Raised when holdings import fails"""
    pass


class SchwabHoldingsParser:
    """Parse Schwab positions/holdings CSV export"""
    
    # Expected columns in Schwab holdings CSV
    REQUIRED_COLUMNS = ['Symbol', 'Qty (Quantity)', 'Cost Basis']
    
    @staticmethod
    def parse_csv(filepath: str) -> pd.DataFrame:
        """Load and parse Schwab holdings CSV
        
        Args:
            filepath: Path to Schwab positions/holdings CSV export
            
        Returns:
            DataFrame with parsed holdings
            
        Raises:
            HoldingsImportError if parsing fails
        """
        try:
            # Schwab positions export has metadata in first 2 rows
            df = pd.read_csv(filepath, skiprows=2)
            
            logger.info(f"Loaded CSV with {len(df)} rows from {filepath}")
            
            # Validate required columns exist
            missing_cols = [col for col in SchwabHoldingsParser.REQUIRED_COLUMNS if col not in df.columns]
            if missing_cols:
                raise HoldingsImportError(f"CSV missing required columns: {missing_cols}")
            
            # Filter to holdings only (exclude Cash and Account Total rows)
            df = df[
                (df['Symbol'] != 'Cash & Cash Investments') & 
                (df['Symbol'] != 'Account Total')
            ].copy()
            
            # Drop rows where Symbol is NaN
            df = df.dropna(subset=['Symbol'])
            
            logger.info(f"Filtered to {len(df)} holdings")
            
            # Normalize data
            df['Symbol'] = df['Symbol'].str.strip().str.upper()
            
            # Parse numeric columns - handle $ and commas
            for col in ['Qty (Quantity)', 'Cost Basis']:
                df[col] = df[col].astype(str).str.replace('$', '').str.replace(',', '')
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Drop rows with NaN in critical columns
            df = df.dropna(subset=['Qty (Quantity)', 'Cost Basis'])
            
            return df
        
        except FileNotFoundError:
            raise HoldingsImportError(f"CSV file not found: {filepath}")
        except Exception as e:
            raise HoldingsImportError(f"Error parsing Schwab holdings CSV: {e}")


class HoldingsImporter:
    """Import holdings/positions into database"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.import_results = {
            'total_holdings': 0,
            'imported': 0,
            'updated': 0,
            'failed': 0,
            'missing_stocks': [],
            'imported_list': [],
            'errors': []
        }
    
    def import_schwab_holdings(self, filepath: str, account: Account, 
                              import_date: date = None) -> Dict:
        """
        Import holdings from Schwab positions CSV.
        
        Args:
            filepath: Path to Schwab positions CSV
            account: Account object to import holdings into
            import_date: Date for this holdings snapshot (defaults to today)
            
        Returns:
            Dict with import results
        """
        if import_date is None:
            import_date = date.today()
        
        self.import_results = {
            'total_holdings': 0,
            'imported': 0,
            'updated': 0,
            'failed': 0,
            'missing_stocks': [],
            'imported_list': [],
            'errors': []
        }
        
        try:
            # Parse CSV
            df = SchwabHoldingsParser.parse_csv(filepath)
            self.import_results['total_holdings'] = len(df)
            
            logger.info(f"Importing {len(df)} holdings for account {account.account_name}")
            
            # Process each holding
            for idx, row in df.iterrows():
                try:
                    ticker = row['Symbol'].strip()
                    quantity = Decimal(str(row['Qty (Quantity)']))
                    cost_basis = Decimal(str(row['Cost Basis']))
                    
                    # Find stock in database
                    stock = self.db.query(Stock).filter_by(ticker=ticker).first()
                    if not stock:
                        logger.warning(f"Row {idx}: Stock {ticker} not found in database, skipping")
                        self.import_results['missing_stocks'].append(ticker)
                        self.import_results['failed'] += 1
                        continue
                    
                    # Check if holding already exists
                    existing = self.db.query(Holding).filter(
                        and_(
                            Holding.account_id == account.id,
                            Holding.stock_id == stock.id
                        )
                    ).first()
                    
                    if existing:
                        # Update existing holding
                        existing.quantity = quantity
                        existing.cost_basis = cost_basis
                        existing.cost_basis_source = 'EXACT'  # From broker CSV
                        existing.import_date = import_date
                        self.db.add(existing)
                        self.import_results['updated'] += 1
                        logger.info(f"  Updated holding: {ticker} - {quantity} shares @ {cost_basis}")
                    else:
                        # Create new holding
                        holding = Holding(
                            account_id=account.id,
                            stock_id=stock.id,
                            quantity=quantity,
                            cost_basis=cost_basis,
                            cost_basis_source='EXACT',  # From broker CSV
                            import_date=import_date,
                            has_complete_history=False  # Will be set after transaction import
                        )
                        self.db.add(holding)
                        self.import_results['imported'] += 1
                        logger.info(f"  Created holding: {ticker} - {quantity} shares @ {cost_basis}")
                    
                    self.import_results['imported_list'].append({
                        'ticker': ticker,
                        'quantity': str(quantity),
                        'cost_basis': str(cost_basis),
                        'action': 'created' if not existing else 'updated'
                    })
                
                except Exception as e:
                    logger.error(f"Row {idx}: Error processing holding: {e}")
                    self.import_results['failed'] += 1
                    self.import_results['errors'].append(f"Row {idx}: {str(e)}")
            
            # Commit all changes
            self.db.commit()
            logger.info(
                f"Import complete: {self.import_results['imported']} created, "
                f"{self.import_results['updated']} updated, "
                f"{self.import_results['failed']} failed"
            )
        
        except HoldingsImportError as e:
            self.import_results['errors'].append(str(e))
            logger.error(f"Import failed: {e}")
            self.db.rollback()
        except Exception as e:
            logger.error(f"Unexpected error during import: {e}")
            self.import_results['errors'].append(str(e))
            self.db.rollback()
        
        return self.import_results
    
    def get_holdings_summary(self, account: Account) -> Dict:
        """Get summary of holdings for an account"""
        holdings = self.db.query(Holding).filter_by(account_id=account.id).all()
        
        summary = {
            'total_holdings': len(holdings),
            'total_shares': sum(h.quantity for h in holdings),
            'total_cost': sum(h.cost_basis for h in holdings),
            'holdings': []
        }
        
        for holding in sorted(holdings, key=lambda h: h.stock.ticker):
            summary['holdings'].append({
                'ticker': holding.stock.ticker,
                'quantity': float(holding.quantity),
                'cost_basis': float(holding.cost_basis),
                'cost_basis_source': holding.cost_basis_source
            })
        
        return summary


def get_import_summary(results: Dict) -> str:
    """Format import results for display"""
    return f"""
Holdings Import Summary:
  Total processed:  {results['total_holdings']}
  ✓ Created:        {results['imported']}
  ✓ Updated:        {results['updated']}
  ✗ Failed:         {results['failed']}
  ⚠ Missing stocks: {len(results['missing_stocks'])}
"""
