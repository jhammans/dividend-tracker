# app/services/ingestion.py
import pandas as pd
from datetime import datetime, timedelta
from app.db.session import SessionLocal
from app.models import Stock, StockPrice, Dividend
from .fetch import fetch_stock_data
from ._helpers import to_native

def ingest_stock_data(tickers: list[str], backfill: bool = True):
    db = SessionLocal()
    try:
        for ticker in tickers:
            stock = db.query(Stock).filter(Stock.ticker == ticker).first()
            if not stock:
                stock = Stock(ticker=ticker)
                db.add(stock)
                db.commit()
                db.refresh(stock)

            # Fetch latest data
            prices_df, dividends_series = fetch_stock_data(ticker)

            # --- Prices ---
            # Force timezone-naive datetime BEFORE renaming
            prices_df.index = (prices_df.index.tz_convert("UTC", nonexistent="shift_forward", ambiguous="NaT").tz_localize(None))
            prices_df = prices_df.reset_index().rename(columns={'Date': 'date'})
            prices_df = to_native(prices_df)

            existing_dates = {row.date for row in db.query(StockPrice.date).filter(StockPrice.stock_id == stock.id)}
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

            # --- Dividends ---
            if not dividends_series.empty:
                dividends_df = dividends_series.reset_index()
                dividends_df = dividends_df.rename(columns={'Date': 'date', dividends_df.columns[1]: 'dividend'})
                dividends_df = to_native(dividends_df)

                existing_div_dates = {row.date for row in db.query(Dividend.date).filter(Dividend.stock_id == stock.id)}
                new_dividends = dividends_df[~dividends_df['date'].isin(existing_div_dates)]

                if backfill and existing_div_dates:
                    # Fill missing dividend dates
                    all_div_dates = pd.date_range(min(dividends_df['date']), max(dividends_df['date']))
                    missing_div_dates = [d.to_pydatetime() for d in all_div_dates if d.to_pydatetime() not in existing_div_dates]
                    if missing_div_dates:
                        missing_divs = dividends_df[dividends_df['date'].isin(missing_div_dates)]
                        new_dividends = pd.concat([new_dividends, missing_divs]).drop_duplicates(subset='date')

                if not new_dividends.empty:
                    db.bulk_insert_mappings(
                        Dividend,
                        [
                            {'stock_id': stock.id, 'date': row['date'], 'dividend': row['dividend']}
                            for _, row in new_dividends.iterrows()
                        ]
                    )

            # Update last_updated timestamp
            stock.last_updated = datetime.utcnow()
            db.add(stock)
            db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
