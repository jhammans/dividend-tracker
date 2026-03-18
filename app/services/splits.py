"""Fetch and store historical stock splits from yfinance for all tickers in DB."""
import logging
from datetime import date
from decimal import Decimal
import yfinance as yf
import pandas as pd
from app.db.session import SessionLocal
from app.models import Stock, StockSplit

logger = logging.getLogger(__name__)


def fetch_and_store_splits(tickers: list[str] | None = None) -> dict:
    """Fetch stock splits from yfinance and upsert into stock_splits table.

    Args:
        tickers: List of tickers to process. If None, processes all stocks in DB.

    Returns:
        dict with 'inserted', 'skipped', 'failed' counts.
    """
    db = SessionLocal()
    inserted = 0
    skipped = 0
    failed = []

    try:
        if tickers is None:
            stocks = db.query(Stock).order_by(Stock.ticker).all()
        else:
            stocks = db.query(Stock).filter(Stock.ticker.in_(tickers)).all()

        total = len(stocks)
        for i, stock in enumerate(stocks, 1):
            try:
                yf_ticker = yf.Ticker(stock.ticker)
                splits_series = yf_ticker.splits

                if splits_series is None or splits_series.empty:
                    logger.debug(f"  [{i}/{total}] {stock.ticker}: no splits")
                    continue

                # Normalize index to date objects
                splits_series.index = pd.to_datetime(splits_series.index).date

                # Load existing split dates for this stock
                existing_dates = {
                    row.date for row in
                    db.query(StockSplit.date).filter(StockSplit.stock_id == stock.id).all()
                }

                new_rows = []
                for split_date, ratio in splits_series.items():
                    if split_date in existing_dates:
                        continue
                    if ratio <= 0:
                        logger.warning(f"  {stock.ticker}: skipping invalid split ratio {ratio} on {split_date}")
                        continue
                    new_rows.append(StockSplit(
                        stock_id=stock.id,
                        date=split_date,
                        ratio=Decimal(str(ratio)),
                        source='YFINANCE',
                    ))

                if new_rows:
                    db.bulk_save_objects(new_rows)
                    db.commit()
                    inserted += len(new_rows)
                    logger.info(f"  [{i}/{total}] {stock.ticker}: inserted {len(new_rows)} split(s)")
                else:
                    skipped += 1
                    logger.debug(f"  [{i}/{total}] {stock.ticker}: all splits already present")

            except Exception as e:
                logger.warning(f"  [{i}/{total}] {stock.ticker}: {e}")
                failed.append((stock.ticker, str(e)))
                db.rollback()

    finally:
        db.close()

    return {'inserted': inserted, 'skipped': skipped, 'failed': failed}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    result = fetch_and_store_splits()
    print(f"\n=== Stock Splits Backfill Complete ===")
    print(f"Inserted: {result['inserted']}")
    print(f"Already present (skipped): {result['skipped']}")
    print(f"Failed: {len(result['failed'])}")
    if result['failed']:
        for ticker, err in result['failed'][:10]:
            print(f"  {ticker}: {err}")
