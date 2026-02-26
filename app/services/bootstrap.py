"""
Bootstrap service to ensure all tickers from a broker CSV are in the database.
This is a prerequisite for transaction and holdings imports to avoid foreign key violations.

Supports flexible column naming: Symbol, Ticker, symbol, ticker, etc.
"""
import logging
import csv
from typing import List, Set, Dict, Tuple, Optional
import pandas as pd
from sqlalchemy.orm import Session

from app.models import Stock
from app.services.fetch import fetch_stock_data, InvalidTickerError
from app.services.ingestion import ingest_stock_data

logger = logging.getLogger(__name__)


class TickerBootstrapError(Exception):
    """Raised when ticker bootstrap fails"""
    pass


class TickerBootstrapper:
    """Ensure all tickers from broker CSV exist in database"""
    
    # Common column names for ticker symbols across brokers
    TICKER_COLUMN_NAMES = [
        'Symbol', 'symbol', 'SYMBOL',
        'Ticker', 'ticker', 'TICKER',
        'Security', 'security', 'SECURITY',
        'Instrument', 'instrument', 'INSTRUMENT',
    ]
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.bootstrap_results = {
            'total_unique_tickers': 0,
            'already_in_db': 0,
            'newly_ingested': 0,
            'failed_ingestion': 0,
            'newly_ingested_list': [],
            'failed_list': [],
            'errors': []
        }
    
    def scan_csv_for_tickers(self, filepath: str, broker: str = 'SCHWAB') -> Set[str]:
        """
        Extract unique tickers from broker CSV.
        
        Args:
            filepath: Path to broker CSV file
            broker: Broker name (used for logging)
        
        Returns:
            Set of ticker symbols (uppercase, stripped of whitespace)
        
        Raises:
            TickerBootstrapError if file read fails or no tickers found
        """
        try:
            tickers = set()
            
            # Use csv.DictReader for brokers with multi-line CSV fields (Robinhood)
            if broker.upper() == 'ROBINHOOD':
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    logger.info(f"Loaded Robinhood CSV using csv.DictReader from {filepath}")
                    
                    # Find ticker column name
                    if not reader.fieldnames:
                        raise TickerBootstrapError("CSV file appears to be empty or invalid")
                    
                    ticker_col = self._find_ticker_column(reader.fieldnames)
                    if not ticker_col:
                        raise TickerBootstrapError(
                            f"Could not find ticker column in CSV. Available columns: {list(reader.fieldnames)}"
                        )
                    
                    logger.info(f"Using column '{ticker_col}' for ticker symbols")
                    
                    # Extract tickers
                    row_count = 0
                    for row in reader:
                        row_count += 1
                        val = row.get(ticker_col, '').strip().upper() if row.get(ticker_col) else ''
                        if val and val not in ['CASH', 'MONEY MARKET', '']:
                            tickers.add(val)
                    
                    logger.info(f"Loaded {row_count} rows from CSV")
            
            else:
                # Use pandas for standard Schwab/Jazz Wealth/Fidelity CSVs
                df = pd.read_csv(filepath)
                logger.info(f"Loaded CSV with {len(df)} rows from {filepath}")
                
                # Find ticker column
                ticker_col = self._find_ticker_column(df.columns)
                if not ticker_col:
                    raise TickerBootstrapError(
                        f"Could not find ticker column in CSV. Available columns: {list(df.columns)}"
                    )
                
                logger.info(f"Using column '{ticker_col}' for ticker symbols")
                
                # Extract and normalize tickers
                for val in df[ticker_col].dropna():
                    ticker = str(val).strip().upper()
                    # Filter out non-ticker values (cash, empty, etc.)
                    if ticker and ticker not in ['CASH', 'MONEY MARKET', '']:
                        tickers.add(ticker)
            
            logger.info(f"Found {len(tickers)} unique ticker symbols")
            self.bootstrap_results['total_unique_tickers'] = len(tickers)
            
            return tickers
        
        except FileNotFoundError:
            raise TickerBootstrapError(f"CSV file not found: {filepath}")
        except Exception as e:
            raise TickerBootstrapError(f"Error reading CSV: {e}")
    
    def _find_ticker_column(self, columns) -> Optional[str]:
        """Find ticker column name from CSV headers"""
        for col in columns:
            if col in self.TICKER_COLUMN_NAMES:
                return col
        return None
    
    def ensure_tickers_in_db(self, tickers: Set[str]) -> Dict:
        """
        Check which tickers are missing from database and ingest them.
        
        Args:
            tickers: Set of ticker symbols to ensure exist
        
        Returns:
            Dict with bootstrap results
        """
        self.bootstrap_results = {
            'total_unique_tickers': len(tickers),
            'already_in_db': 0,
            'newly_ingested': 0,
            'failed_ingestion': 0,
            'newly_ingested_list': [],
            'failed_list': [],
            'errors': []
        }
        
        # Check which tickers are already in database
        existing_tickers = set(
            row[0] for row in self.db.query(Stock.ticker).filter(
                Stock.ticker.in_(list(tickers))
            ).all()
        )
        
        self.bootstrap_results['already_in_db'] = len(existing_tickers)
        logger.info(f"Found {len(existing_tickers)} tickers already in database")
        
        # Find missing tickers
        missing_tickers = tickers - existing_tickers
        if not missing_tickers:
            logger.info("All tickers already in database, nothing to ingest")
            return self.bootstrap_results
        
        logger.info(f"Need to ingest {len(missing_tickers)} new tickers: {sorted(missing_tickers)}")
        
        # Ingest missing tickers using existing ingestion pipeline
        try:
            results = ingest_stock_data(list(missing_tickers))
            
            # Parse results
            if isinstance(results, dict):
                self.bootstrap_results['newly_ingested'] = len(results.get('successful', []))
                self.bootstrap_results['failed_ingestion'] = len(results.get('failed', []))
                self.bootstrap_results['newly_ingested_list'] = results.get('successful', [])
                self.bootstrap_results['failed_list'] = results.get('failed', [])
                
                if results.get('errors'):
                    self.bootstrap_results['errors'] = results.get('errors', [])
            
            logger.info(
                f"Ingestion complete: {self.bootstrap_results['newly_ingested']} successful, "
                f"{self.bootstrap_results['failed_ingestion']} failed"
            )
            
        except Exception as e:
            logger.error(f"Error during ticker ingestion: {e}")
            self.bootstrap_results['errors'].append(str(e))
            self.bootstrap_results['failed_ingestion'] = len(missing_tickers)
            self.bootstrap_results['failed_list'] = list(missing_tickers)
        
        return self.bootstrap_results
    
    def bootstrap_from_csv(self, filepath: str, broker: str = 'SCHWAB') -> Dict:
        """
        One-shot bootstrap: scan CSV and ensure all tickers are in database.
        
        Args:
            filepath: Path to broker CSV file
            broker: Broker name (for logging)
        
        Returns:
            Dict with bootstrap results
        """
        logger.info(f"Starting ticker bootstrap for {broker} CSV: {filepath}")
        
        try:
            # Scan for tickers
            tickers = self.scan_csv_for_tickers(filepath, broker)
            
            # Ensure all tickers are in database
            results = self.ensure_tickers_in_db(tickers)
            
            # Log summary
            self._log_bootstrap_summary(results)
            
            return results
        
        except TickerBootstrapError as e:
            logger.error(f"Bootstrap failed: {e}")
            self.bootstrap_results['errors'].append(str(e))
            return self.bootstrap_results
        except Exception as e:
            logger.error(f"Unexpected error during bootstrap: {e}")
            self.bootstrap_results['errors'].append(str(e))
            return self.bootstrap_results
    
    def _log_bootstrap_summary(self, results: Dict) -> None:
        """Log a formatted summary of bootstrap results"""
        summary = f"""
╔════════════════════════════════════════════════════════╗
║           Ticker Bootstrap Summary                     ║
╠════════════════════════════════════════════════════════╣
║ Total unique tickers: {results['total_unique_tickers']:>33} ║
║ Already in database:  {results['already_in_db']:>33} ║
║ Newly ingested:       {results['newly_ingested']:>33} ║
║ Failed ingestion:     {results['failed_ingestion']:>33} ║
╚════════════════════════════════════════════════════════╝"""
        
        logger.info(summary)
        
        if results['newly_ingested_list']:
            logger.info(f"Newly ingested tickers: {sorted(results['newly_ingested_list'])}")
        
        if results['failed_list']:
            logger.warning(f"Failed to ingest: {sorted(results['failed_list'])}")
        
        if results['errors']:
            logger.error("Errors encountered:")
            for error in results['errors']:
                logger.error(f"  - {error}")


def bootstrap_csv(filepath: str, db_session: Session, broker: str = 'SCHWAB') -> Dict:
    """
    Convenience function to bootstrap tickers from a CSV file.
    
    Usage:
        from app.db.session import SessionLocal
        from app.services.bootstrap import bootstrap_csv
        
        db = SessionLocal()
        results = bootstrap_csv('/path/to/schwab_2026.csv', db)
        print(results)
    """
    bootstrapper = TickerBootstrapper(db_session)
    return bootstrapper.bootstrap_from_csv(filepath, broker)
