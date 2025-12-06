import logging
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from app.db.session import SessionLocal
from app.models import Stock, StockPrice, Dividend

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = SessionLocal()


def add_stock(ticker: str) -> Stock:
    """Add a stock if it doesn't exist, or update missing metadata."""
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()

    if stock:
        logger.info(f"Stock {ticker} already exists. Checking metadata...")
    else:
        stock = Stock(ticker=ticker)
        db.add(stock)
        db.commit()
        db.refresh(stock)
        logger.info(f"Added stock {ticker}.")

    # Backfill metadata from Yahoo Finance
    yf_stock = yf.Ticker(ticker)
    info = yf_stock.info

    stock.name = stock.name or info.get('shortName')
    stock.exchange = stock.exchange or info.get('exchange')
    stock.sector = stock.sector or info.get('sector')
    stock.industry = stock.industry or info.get('industry')
    stock.currency = stock.currency or info.get('currency')
    stock.last_updated = datetime.utcnow()

    db.commit()
    logger.info(f"Metadata updated for {ticker}.")

    return stock


def to_native(value):
    """Convert Pandas/Numpy types to native Python types for Postgres."""
    if isinstance(value, (np.generic, np.ndarray)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def fetch_stock_data(stock: Stock, period: str = "1y"):
    """Fetch stock prices and dividends and insert into DB safely."""
    yf_stock = yf.Ticker(stock.ticker)
    history = yf_stock.history(period=period)
    dividends = yf_stock.dividends

    # --- Stock Prices ---
    existing_price_dates = set(
        r[0] for r in db.query(StockPrice.date)
                        .filter(StockPrice.stock_id == stock.id)
                        .all()
    )

    new_prices = []
    for date, row in history.iterrows():
        price_date = to_native(date)
        if price_date in existing_price_dates:
            continue

        sp = StockPrice(
            stock_id=stock.id,
            date=price_date,
            open=to_native(row.get('Open', 0.0)),
            high=to_native(row.get('High', 0.0)),
            low=to_native(row.get('Low', 0.0)),
            close=to_native(row.get('Close', 0.0)),
            volume=int(to_native(row.get('Volume', 0)))
        )
        new_prices.append(sp)

    if new_prices:
        db.bulk_save_objects(new_prices)
        logger.info(f"Inserted {len(new_prices)} new price rows for {stock.ticker}.")

    # --- Dividends ---
    existing_div_dates = set(
        r[0] for r in db.query(Dividend.date)
                        .filter(Dividend.stock_id == stock.id)
                        .all()
    )

    new_dividends = []
    for date, amount in dividends.items():
        div_date = to_native(date)
        if div_date in existing_div_dates:
            continue

        div = Dividend(
            stock_id=stock.id,
            date=div_date,
            amount=float(to_native(amount)) if amount is not None else 0.0,
            ex_date=None,
            pay_date=None,
            record_date=None,
            declared_date=None,
            frequency=None
        )
        new_dividends.append(div)

    if new_dividends:
        db.bulk_save_objects(new_dividends)
        logger.info(f"Inserted {len(new_dividends)} new dividend rows for {stock.ticker}.")

    db.commit()
    logger.info(f"Fetched data for {stock.ticker}: {len(new_prices)} prices, {len(new_dividends)} dividends.")


def ingest_stocks(tickers: list, period: str = "1y"):
    """Ingest multiple tickers and update stock metadata."""
    for ticker in tickers:
        stock = add_stock(ticker)
        fetch_stock_data(stock, period)
