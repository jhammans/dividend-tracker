# app/services/ingestion.py
import pandas as pd
from datetime import datetime, timedelta
import logging
import yfinance as yf
from app.db.session import SessionLocal
from app.models import Stock, StockPrice, Dividend
from .fetch import fetch_stock_data, InvalidTickerError
from ._helpers import to_native

logger = logging.getLogger(__name__)

def ingest_stock_data(tickers: list[str], backfill: bool = True):
    """Ingest stock data for multiple tickers, skipping invalid ones."""
    db = SessionLocal()
    successful = []
    failed = []
    
    try:
        for ticker in tickers:
            try:
                logger.info(f"Processing {ticker}...")
                
                # Fetch latest data first to validate ticker
                try:
                    prices_df, dividends_series = fetch_stock_data(ticker)
                except InvalidTickerError as e:
                    logger.warning(f"Skipping {ticker}: {e}")
                    failed.append((ticker, str(e)))
                    continue
                
                # Fetch stock metadata from yfinance
                try:
                    yf_stock = yf.Ticker(ticker)
                    yf_info = yf_stock.info
                    asset_type = yf_info.get('quoteType', 'EQUITY')
                    name = yf_info.get('longName') or yf_info.get('shortName')
                    exchange = yf_info.get('exchange')
                    sector = yf_info.get('sector')
                    industry = yf_info.get('industry')
                except Exception as e:
                    logger.warning(f"Could not fetch yfinance metadata for {ticker}: {e}")
                    asset_type, name, exchange, sector, industry = 'EQUITY', None, None, None, None
                
                # Create or update stock record
                stock = db.query(Stock).filter(Stock.ticker == ticker).first()
                if not stock:
                    # Create new stock with fetched metadata
                    stock = Stock(
                        ticker=ticker,
                        name=name,
                        exchange=exchange,
                        sector=sector,
                        industry=industry,
                        asset_type=asset_type
                    )
                    db.add(stock)
                    db.commit()
                    db.refresh(stock)
                    logger.info(f"  Created new stock: {ticker} ({asset_type})")
                else:
                    # Check for updates to existing stock
                    updates = {}
                    if stock.name != name:
                        updates['name'] = (stock.name, name)
                    if stock.exchange != exchange:
                        updates['exchange'] = (stock.exchange, exchange)
                    if stock.sector != sector:
                        updates['sector'] = (stock.sector, sector)
                    if stock.industry != industry:
                        updates['industry'] = (stock.industry, industry)
                    if stock.asset_type != asset_type:
                        updates['asset_type'] = (stock.asset_type, asset_type)
                    
                    if updates:
                        # Apply updates
                        if 'name' in updates:
                            stock.name = name
                        if 'exchange' in updates:
                            stock.exchange = exchange
                        if 'sector' in updates:
                            stock.sector = sector
                        if 'industry' in updates:
                            stock.industry = industry
                        if 'asset_type' in updates:
                            stock.asset_type = asset_type
                        
                        db.commit()
                        logger.info(f"  Updated stock: {ticker}")
                        for field, (old, new) in updates.items():
                            logger.info(f"    {field}: {old} → {new}")

                # --- Prices ---
                # Handle timezone conversion safely (yfinance may return naive or tz-aware)
                if prices_df.index.tz is not None:
                    prices_df.index = prices_df.index.tz_convert("UTC").tz_localize(None)
                prices_df = prices_df.reset_index().rename(columns={'Date': 'date'})
                prices_df = to_native(prices_df)
                # Ensure dates are Python date objects (not timestamps)
                prices_df['date'] = pd.to_datetime(prices_df['date']).dt.date

                existing_dates = {row.date for row in db.query(StockPrice).filter(StockPrice.stock_id == stock.id).all()}
                new_prices = prices_df[~prices_df['date'].isin(existing_dates)]

                if backfill and existing_dates:
                    # Find missing dates
                    all_dates = pd.date_range(min(prices_df['date']), max(prices_df['date']))
                    missing_dates = [d.to_pydatetime() for d in all_dates if d.to_pydatetime() not in existing_dates]
                    if missing_dates:
                        missing_prices = prices_df[prices_df['date'].isin(missing_dates)]
                        new_prices = pd.concat([new_prices, missing_prices]).drop_duplicates(subset='date')

                if not new_prices.empty:
                    db.bulk_insert_mappings(
                        StockPrice,
                        [
                            {
                                'stock_id': stock.id,
                                'date': row['date'],
                                'open': row['open'],
                                'high': row['high'],
                                'low': row['low'],
                                'close': row['close'],
                                'volume': row['volume']
                            }
                            for _, row in new_prices.iterrows()
                        ]
                    )
                    logger.info(f"  Inserted {len(new_prices)} price records for {ticker}")

                # --- Dividends ---
                if not dividends_series.empty:
                    dividends_df = dividends_series.reset_index()
                    dividends_df = dividends_df.rename(columns={'Date': 'ex_date', dividends_df.columns[1]: 'amount'})
                    dividends_df = to_native(dividends_df)
                    # Ensure dates are Python date objects (not timestamps)
                    dividends_df['ex_date'] = pd.to_datetime(dividends_df['ex_date']).dt.date

                    # Query existing ex_dates for this stock
                    existing_ex_dates = {row.ex_date for row in db.query(Dividend).filter(Dividend.stock_id == stock.id).all()}
                    new_dividends = dividends_df[~dividends_df['ex_date'].isin(existing_ex_dates)]

                    if not new_dividends.empty:
                        db.bulk_insert_mappings(
                            Dividend,
                            [
                                {'stock_id': stock.id, 'ex_date': row['ex_date'], 'amount': row['amount']}
                                for _, row in new_dividends.iterrows()
                            ]
                        )
                        logger.info(f"  Inserted {len(new_dividends)} dividend records for {ticker}")
                    db.commit()

                # Update last_updated timestamp
                stock.last_updated = datetime.utcnow()
                db.add(stock)
                db.commit()
                
                successful.append(ticker)
                logger.info(f"✓ Successfully ingested {ticker}")
                
            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}")
                failed.append((ticker, str(e)))
                db.rollback()
                continue
        
    finally:
        db.close()
        
    # Summary report
    if successful or failed:
        print(f"\n=== Ingestion Summary ===")
        if successful:
            print(f"✓ Successful ({len(successful)}): {', '.join(successful)}")
        if failed:
            print(f"✗ Failed ({len(failed)}):")
            for ticker, reason in failed:
                print(f"  - {ticker}: {reason}")
