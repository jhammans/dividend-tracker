"""
Integration tests for the bootstrap service.

Requires a live PostgreSQL connection (DATABASE_URL in .env).
Excluded from CI — run manually: pytest test_bootstrap.py -m integration
"""
import csv
import logging
import pytest
from tempfile import NamedTemporaryFile
from app.db.session import SessionLocal
from app.services.bootstrap import bootstrap_csv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def create_sample_schwab_csv():
    """Create a sample Schwab transaction CSV for testing"""
    
    # Sample data with some tickers already in DB and some new ones
    sample_data = [
        ['Date', 'Type', 'Symbol', 'Quantity', 'Price', 'Amount'],
        # Existing tickers
        ['01/15/2026', 'BUY', 'AAPL', '10', '150.00', '$1,500.00'],
        ['01/20/2026', 'BUY', 'KO', '25', '62.50', '$1,562.50'],
        ['02/01/2026', 'Dividend', 'SCHD', '50', '3.50', '$175.00'],
        # New tickers to be ingested
        ['02/05/2026', 'BUY', 'NVDA', '5', '875.00', '$4,375.00'],
        ['02/10/2026', 'BUY', 'TSLA', '3', '245.00', '$735.00'],
        ['02/15/2026', 'Reinvest Dividend', 'VTI', '1.5', '220.00', '$330.00'],
    ]
    
    # Write to temporary CSV file
    with NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
        writer = csv.writer(f)
        writer.writerows(sample_data)
        return f.name


@pytest.mark.integration
def test_bootstrap():
    """Test the bootstrap service"""
    
    logger.info("Creating sample Schwab CSV...")
    csv_path = create_sample_schwab_csv()
    logger.info(f"Sample CSV created at: {csv_path}")
    
    logger.info("\nStarting ticker bootstrap...")
    logger.info("=" * 70)
    
    db = SessionLocal()
    try:
        results = bootstrap_csv(csv_path, db, broker='SCHWAB')
        
        logger.info("\n" + "=" * 70)
        logger.info("Bootstrap Complete!")
        logger.info("=" * 70)
        
        logger.info(f"\nResults:")
        logger.info(f"  Total tickers found:  {results['total_unique_tickers']}")
        logger.info(f"  Already in DB:        {results['already_in_db']}")
        logger.info(f"  Newly ingested:       {results['newly_ingested']}")
        logger.info(f"  Failed ingestion:     {results['failed_ingestion']}")
        
        if results['newly_ingested_list']:
            logger.info(f"\n✓ Successfully ingested: {sorted(results['newly_ingested_list'])}")
        
        if results['failed_list']:
            logger.info(f"\n✗ Failed to ingest: {sorted(results['failed_list'])}")
        
        if results['errors']:
            logger.error("\nErrors encountered:")
            for error in results['errors']:
                logger.error(f"  - {error}")
        
        # Verify tickers are now in database
        logger.info("\n" + "=" * 70)
        logger.info("Verification: Querying database for all ingested tickers...")
        logger.info("=" * 70)
        
        from app.models import Stock
        
        all_tickers = [
            'AAPL', 'KO', 'SCHD', 'NVDA', 'TSLA', 'VTI',
            # Also show existing ones
            'BND', 'GLD', 'QQQM', 'SPYG', 'VGT', 'VTV', 'MSFT', 'UNH'
        ]
        
        logger.info("\nAll tickers in database:")
        for ticker in sorted(all_tickers):
            stock = db.query(Stock).filter_by(ticker=ticker).first()
            if stock:
                logger.info(f"  ✓ {ticker:6s} - {stock.asset_type:10s} - {stock.name or '(metadata pending)'}")
            else:
                logger.info(f"  ✗ {ticker:6s} - NOT FOUND")
    
    finally:
        db.close()
        import os
        os.unlink(csv_path)
        logger.info(f"\nCleanup: Removed temporary CSV")


if __name__ == '__main__':
    logger.info("=" * 70)
    logger.info("Ticker Bootstrap Test")
    logger.info("=" * 70)
    logger.info("""
This test demonstrates the ticker bootstrap service which:
1. Scans a broker CSV for all unique ticker symbols
2. Identifies which tickers are missing from the database
3. Ingests missing tickers with metadata from yfinance
4. Reports results

This is a required prerequisite before importing holdings or transactions,
as it ensures all foreign key references will resolve correctly.
    """)
    logger.info("=" * 70 + "\n")
    
    test_bootstrap()
