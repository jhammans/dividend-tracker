#!/usr/bin/env python3
"""
Dividend Tracker Import Pipeline
Handles: Bootstrap → Holdings Import → Transaction Import

Usage:
    python import_pipeline.py bootstrap <csv_path>
    python import_pipeline.py holdings <csv_path> <account_id>
    python import_pipeline.py transactions <csv_path> <account_id>
    python import_pipeline.py full <holdings_csv> <transactions_csv> <account_id>    python import_pipeline.py refresh [--tickers AAPL MSFT ...]"""
import sys
import os
import argparse
import logging
from pathlib import Path
from app.db.session import SessionLocal
from app.models import Account, Stock
from app.services.bootstrap import bootstrap_csv
from app.services.holdings import HoldingsImporter
from app.services.transactions import TransactionImporter
from app.services.ingestion import ingest_stock_data

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def validate_file(filepath):
    """Ensure file exists, is a regular file, and is readable.

    Resolves symlinks and normalises the path to prevent directory traversal
    tricks such as `../../etc/passwd`.  The resolved path is returned so
    callers always work with an absolute, canonical path.
    """
    path = Path(filepath).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    if not path.is_file():
        raise ValueError(f"Not a regular file: {filepath}")
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Expected a .csv file, got: {filepath}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"File is not readable: {filepath}")
    return str(path)


def validate_account(db, account_id):
    """Ensure account exists"""
    account = db.query(Account).filter_by(id=account_id).first()
    if not account:
        raise ValueError(f"Account ID {account_id} not found in database")
    return account


def cmd_refresh(args):
    """Refresh yfinance data (prices, dividends, splits, metadata) for all stocks."""
    db = SessionLocal()
    try:
        if args.tickers:
            tickers = [t.upper() for t in args.tickers]
            logger.info(f"Refreshing {len(tickers)} specified ticker(s)...")
        else:
            tickers = [row.ticker for row in db.query(Stock.ticker).order_by(Stock.ticker).all()]
            logger.info(f"Refreshing all {len(tickers)} stocks in database...")

        logger.info("-" * 70)
        results = ingest_stock_data(tickers, backfill=True)
        logger.info("-" * 70)

        successful = results['successful']
        failed = results['failed']

        logger.info(f"✓ Refresh Complete")
        logger.info(f"  Refreshed:  {len(successful)} / {len(tickers)}")
        logger.info(f"  Failed:     {len(failed)}")

        if failed:
            logger.warning("\nFailed tickers:")
            for ticker, reason in failed:
                logger.warning(f"  {ticker:10s}  {reason}")

        return 0 if not failed else 1

    finally:
        db.close()


def cmd_bootstrap(args):
    """Bootstrap: Scan CSV and ensure all tickers exist in database"""
    csv_path = validate_file(args.csv)
    
    db = SessionLocal()
    try:
        logger.info(f"Starting bootstrap for: {args.csv}")
        logger.info("-" * 70)
        
        results = bootstrap_csv(csv_path, db, broker=args.broker)
        
        logger.info("-" * 70)
        logger.info(f"✓ Bootstrap Complete")
        logger.info(f"  Total unique tickers:  {results['total_unique_tickers']}")
        logger.info(f"  Already in DB:         {results['already_in_db']}")
        logger.info(f"  Newly ingested:        {results['newly_ingested']}")
        logger.info(f"  Failed ingestion:      {results['failed_ingestion']}")
        
        if results['newly_ingested_list']:
            logger.info(f"\n✓ Successfully ingested: {sorted(results['newly_ingested_list'])}")
        
        if results['failed_list']:
            logger.warning(f"\n✗ Failed to ingest: {sorted(results['failed_list'])}")
        
        if results['errors']:
            logger.error("\nErrors encountered:")
            for error in results['errors']:
                logger.error(f"  - {error}")
        
        return 0 if results['failed_ingestion'] == 0 else 1
        
    finally:
        db.close()


def cmd_holdings(args):
    """Holdings: Import current positions with cost basis"""
    csv_path = validate_file(args.csv)
    
    db = SessionLocal()
    try:
        # Bootstrap first
        logger.info(f"[Step 1/2] Bootstrap tickers from holdings CSV...")
        results = bootstrap_csv(csv_path, db, broker=args.broker)
        logger.info(f"  ✓ {results['total_unique_tickers']} tickers verified")
        
        # Get account
        logger.info(f"\n[Step 2/2] Import holdings into account {args.account}...")
        account = validate_account(db, args.account)
        logger.info(f"  ✓ Account: {account.account_name} ({account.broker})")
        
        # Import holdings
        importer = HoldingsImporter(db)
        results = importer.import_schwab_holdings(csv_path, account)
        
        logger.info("-" * 70)
        logger.info(f"✓ Holdings Import Complete")
        logger.info(f"  Total holdings:        {results['total_holdings']}")
        logger.info(f"  Successfully created:  {results['imported']}")
        logger.info(f"  Updated existing:      {results['updated']}")
        logger.info(f"  Failed:                {results['failed']}")
        
        if results['errors']:
            logger.error("\nErrors:")
            for error in results['errors'][:5]:
                logger.error(f"  - {error}")
        
        return 0 if results['failed'] == 0 else 1
        
    finally:
        db.close()


def cmd_transactions(args):
    """Transactions: Import buy/sell activity with duplicate detection"""
    csv_path = validate_file(args.csv)
    
    db = SessionLocal()
    try:
        # Bootstrap first
        logger.info(f"[Step 1/2] Bootstrap tickers from transactions CSV...")
        results = bootstrap_csv(csv_path, db, broker=args.broker)
        logger.info(f"  ✓ {results['total_unique_tickers']} tickers verified")
        
        # Get account
        logger.info(f"\n[Step 2/2] Import transactions into account {args.account}...")
        account = validate_account(db, args.account)
        logger.info(f"  ✓ Account: {account.account_name} ({account.broker})")
        
        # Import transactions (broker-specific)
        importer = TransactionImporter(db)
        
        if args.broker.upper() in ['GOLDMAN_SACHS', 'JAZZWEALTH']:
            logger.info(f"  Using Jazz Wealth parser...")
            results = importer.import_jazzwealth_transactions(csv_path, account)
        elif args.broker.upper() == 'ROBINHOOD':
            logger.info(f"  Using Robinhood parser...")
            results = importer.import_robinhood_transactions(csv_path, account)
        else:
            logger.info(f"  Using {args.broker} parser...")
            results = importer.import_schwab_transactions(csv_path, account)
        
        logger.info("-" * 70)
        logger.info(f"✓ Transaction Import Complete")
        logger.info(f"  Total processed:       {results['total_processed']}")
        logger.info(f"  Imported (buy/sell):   {results['imported_count']}")
        logger.info(f"  Skipped (divid/etc):   {results['skipped_count']}")
        logger.info(f"  Failed:                {results['failed_count']}")
        
        if results['duplicates_skipped'] > 0:
            logger.info(f"  Duplicates skipped:    {results['duplicates_skipped']}")
        
        # Show imported transactions by type
        if results['imported_transactions']:
            buy_txns = [t for t in results['imported_transactions'] if t['type'] == 'BUY']
            sell_txns = [t for t in results['imported_transactions'] if t['type'] == 'SELL']
            
            if buy_txns:
                logger.info(f"\nBUY transactions ({len(buy_txns)}):")
                for txn in buy_txns[:5]:
                    logger.info(f"  {txn['date']} | {txn['symbol']:6s} | {float(txn['quantity']):>10.2f} @ ${float(txn.get('price', -1)) if isinstance(txn.get('price'), str) else txn.get('price', -1):.2f}")
                if len(buy_txns) > 5:
                    logger.info(f"  ... and {len(buy_txns) - 5} more")
            
            if sell_txns:
                logger.info(f"\nSELL transactions ({len(sell_txns)}):")
                for txn in sell_txns[:5]:
                    logger.info(f"  {txn['date']} | {txn['symbol']:6s} | {float(txn['quantity']):>10.2f} @ ${float(txn.get('price', -1)) if isinstance(txn.get('price'), str) else txn.get('price', -1):.2f}")
                if len(sell_txns) > 5:
                    logger.info(f"  ... and {len(sell_txns) - 5} more")
        
        if results['errors']:
            logger.error("\nErrors:")
            for error in results['errors'][:5]:
                logger.error(f"  - {error}")
        
        return 0 if results['failed_count'] == 0 else 1
        
    finally:
        db.close()


def cmd_full(args):
    """Full Pipeline: Bootstrap → Holdings → Transactions"""
    holdings_csv = validate_file(args.holdings_csv)
    transactions_csv = validate_file(args.transactions_csv)
    
    db = SessionLocal()
    try:
        account = validate_account(db, args.account)
        
        # Step 1: Bootstrap all tickers
        logger.info("="*70)
        logger.info(f"[Step 1/3] Bootstrap tickers from both CSVs...")
        logger.info("="*70)
        
        results1 = bootstrap_csv(holdings_csv, db, broker=args.broker)
        logger.info(f"Holdings CSV: {results1['total_unique_tickers']} tickers verified")
        
        results2 = bootstrap_csv(transactions_csv, db, broker=args.broker)
        logger.info(f"Transactions CSV: {results2['total_unique_tickers']} tickers verified")
        
        # Step 2: Import holdings
        logger.info("\n" + "="*70)
        logger.info(f"[Step 2/3] Import holdings snapshot...")
        logger.info("="*70)
        
        holdings_importer = HoldingsImporter(db)
        holdings_results = holdings_importer.import_schwab_holdings(holdings_csv, account)
        logger.info(f"✓ Imported {holdings_results['imported']} holdings ({holdings_results['updated']} updated)")
        
        # Step 3: Import transactions
        logger.info("\n" + "="*70)
        logger.info(f"[Step 3/3] Import transactions...")
        logger.info("="*70)
        
        transactions_importer = TransactionImporter(db)
        
        if args.broker.upper() in ['GOLDMAN_SACHS', 'JAZZWEALTH']:
            logger.info(f"Using Jazz Wealth parser...")
            transactions_results = transactions_importer.import_jazzwealth_transactions(
                transactions_csv, account
            )
        elif args.broker.upper() == 'ROBINHOOD':
            logger.info(f"Using Robinhood parser...")
            transactions_results = transactions_importer.import_robinhood_transactions(
                transactions_csv, account
            )
        else:
            logger.info(f"Using {args.broker} parser...")
            transactions_results = transactions_importer.import_schwab_transactions(
                transactions_csv, account
            )
        
        logger.info(f"✓ Imported {transactions_results['imported_count']} transactions")
        logger.info(f"✓ Skipped {transactions_results['skipped_count']} non-trade events")
        
        # Summary
        logger.info("\n" + "="*70)
        logger.info("✓ FULL PIPELINE COMPLETE")
        logger.info("="*70)
        logger.info(f"\nAccount: {account.account_name} ({account.broker})")
        logger.info(f"Holdings imported: {holdings_results['imported']} created, {holdings_results['updated']} updated")
        logger.info(f"Transactions imported: {transactions_results['imported_count']}")
        logger.info(f"  - Buys/Sells/Dividends: {transactions_results['imported_count']}")
        logger.info(f"  - Skipped (non-security): {transactions_results['skipped_count']}")
        
        return 0 if (holdings_results['failed'] == 0 and 
                     transactions_results['failed_count'] == 0) else 1
        
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description='Dividend Tracker: Import pipeline for Schwab broker data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create accounts
  python import_pipeline.py create-account "Schwab Taxable" --broker SCHWAB --type TAXABLE
  python import_pipeline.py create-account "Goldman Sachs Roth IRA" --broker GOLDMAN_SACHS --type ROTH_IRA --number XXX612
  
  # List all accounts
  python import_pipeline.py accounts
  
  # Bootstrap all tickers (prerequisite for holdings/transactions)
  python import_pipeline.py bootstrap ~/Downloads/my_holdings.csv
  
  # Import current holdings
  python import_pipeline.py holdings ~/Downloads/my_holdings.csv 2
  
  # Import transactions
  python import_pipeline.py transactions ~/Downloads/my_transactions.csv 2
  
  # All at once
  python import_pipeline.py full ~/Downloads/holdings.csv ~/Downloads/transactions.csv 2

  # Refresh all yfinance data (prices, dividends, splits)
  python import_pipeline.py refresh

  # Refresh specific tickers only
  python import_pipeline.py refresh --tickers AAPL MSFT SCHD
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Bootstrap command
    bootstrap_parser = subparsers.add_parser('bootstrap', help='Scan CSV for tickers')
    bootstrap_parser.add_argument('csv', help='Path to holdings or transactions CSV')
    bootstrap_parser.add_argument('--broker', default='SCHWAB', 
                                 choices=['SCHWAB', 'FIDELITY', 'GOLDMAN_SACHS', 'ROBINHOOD', 'OTHER'],
                                 help='Broker name (default: SCHWAB)')
    bootstrap_parser.set_defaults(func=cmd_bootstrap)
    
    # Holdings command
    holdings_parser = subparsers.add_parser('holdings', help='Import holdings snapshot')
    holdings_parser.add_argument('csv', help='Path to Schwab holdings/positions CSV')
    holdings_parser.add_argument('account', type=int, help='Account ID in database')
    holdings_parser.add_argument('--broker', default='SCHWAB',
                                 choices=['SCHWAB', 'FIDELITY', 'GOLDMAN_SACHS', 'ROBINHOOD', 'OTHER'],
                                 help='Broker name (default: SCHWAB)')
    holdings_parser.set_defaults(func=cmd_holdings)
    
    # Transactions command
    transactions_parser = subparsers.add_parser('transactions', help='Import transactions')
    transactions_parser.add_argument('csv', help='Path to transactions CSV')
    transactions_parser.add_argument('account', type=int, help='Account ID in database')
    transactions_parser.add_argument('--broker', default='SCHWAB',
                                     choices=['SCHWAB', 'FIDELITY', 'GOLDMAN_SACHS', 'ROBINHOOD', 'OTHER'],
                                     help='Broker/CSV format (default: SCHWAB)')
    transactions_parser.set_defaults(func=cmd_transactions)
    
    # Full pipeline command
    full_parser = subparsers.add_parser('full', help='Run complete pipeline')
    full_parser.add_argument('holdings_csv', help='Path to holdings CSV')
    full_parser.add_argument('transactions_csv', help='Path to transactions CSV')
    full_parser.add_argument('account', type=int, help='Account ID in database')
    full_parser.add_argument('--broker', default='SCHWAB',
                             choices=['SCHWAB', 'FIDELITY', 'GOLDMAN_SACHS', 'ROBINHOOD', 'OTHER'],
                             help='Broker/CSV format (default: SCHWAB)')
    full_parser.set_defaults(func=cmd_full)
    
    # Refresh command
    refresh_parser = subparsers.add_parser(
        'refresh',
        help='Re-fetch yfinance data (prices, dividends, splits) for all stocks'
    )
    refresh_parser.add_argument(
        '--tickers', nargs='+', metavar='TICKER',
        help='Refresh only these tickers (default: all stocks in DB)'
    )
    refresh_parser.set_defaults(func=cmd_refresh)

    # Show database accounts
    accounts_parser = subparsers.add_parser('accounts', help='List available accounts')
    accounts_parser.set_defaults(func=cmd_accounts)
    
    # Create account command
    create_account_parser = subparsers.add_parser('create-account', help='Create a new account')
    create_account_parser.add_argument('name', help='Account name (e.g., "Schwab Taxable")')
    create_account_parser.add_argument('--broker', default='SCHWAB', 
                                       choices=['SCHWAB', 'FIDELITY', 'GOLDMAN_SACHS', 'ROBINHOOD', 'OTHER'],
                                       help='Broker name (default: SCHWAB)')
    create_account_parser.add_argument('--type', dest='account_type', default='TAXABLE',
                                       choices=['TAXABLE', 'IRA', 'ROTH_IRA', '401K', 'OTHER'],
                                       help='Account type (default: TAXABLE)')
    create_account_parser.add_argument('--number', dest='account_number', default='',
                                       help='Account number (optional, last 4 digits recommended)')
    create_account_parser.set_defaults(func=cmd_create_account)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        return args.func(args)
    except Exception as e:
        logger.error(f"✗ Error: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return 1


def cmd_create_account(args):
    """Create a new account in the database"""
    db = SessionLocal()
    try:
        # Check if account already exists
        existing = db.query(Account).filter_by(
            broker=args.broker,
            account_name=args.name
        ).first()
        
        if existing:
            logger.warning(f"✗ Account already exists: ID {existing.id}")
            return 1
        
        # Create new account
        account = Account(
            broker=args.broker,
            account_name=args.name,
            account_type=args.account_type,
            account_number=args.account_number or None
        )
        db.add(account)
        db.commit()
        
        logger.info(f"✓ Account created successfully")
        logger.info(f"  ID:      {account.id}")
        logger.info(f"  Name:    {account.account_name}")
        logger.info(f"  Broker:  {account.broker}")
        logger.info(f"  Type:    {account.account_type}")
        if account.account_number:
            logger.info(f"  Number:  {account.account_number}")
        
        return 0
        
    except Exception as e:
        logger.error(f"✗ Failed to create account: {e}")
        return 1
    finally:
        db.close()


def cmd_accounts(args):
    """List available accounts in database"""
    db = SessionLocal()
    try:
        accounts = db.query(Account).all()
        if not accounts:
            logger.info("No accounts found in database")
            return 1
        
        logger.info("Available accounts:")
        for acc in accounts:
            logger.info(f"  ID {acc.id}: {acc.account_name} ({acc.broker} - {acc.account_type})")
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
