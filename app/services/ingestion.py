import logging
import yfinance as yf
import pandas as pd
from app.db.session import SessionLocal
from app.models import Stock
from app.models import StockPrice
from app.models import Dividend
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = SessionLocal()

def add_stock(ticker: str, name: str = None):
    existing = db.query(Stock).filter(Stock.ticker == ticker).first()
    if existing:
        logger.info(f"Stock {ticker} already exists.")
        return existing
    stock = Stock(ticker=ticker, name=name)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    logger.info(f"Added stock {ticker}.")
    return stock

def fetch_stock_data(stock: Stock, period: str = "1y"):
    yf_stock = yf.Ticker(stock.ticker)
    history = yf_stock.history(period=period)
    dividends = yf_stock.dividends

    # Insert price history
    for date, row in history.iterrows():
        sp = StockPrice(
            stock_id=stock.id,
            date=date.to_pydatetime(),
            open=row['Open'],
            close=row['Close'],
            high=row['High'],
            low=row['Low'],
            volume=row['Volume']
        )
        db.add(sp)

    # Insert dividends
    for date, amount in dividends.items():
        div = Dividend(
            stock_id=stock.id,
            date=date.to_pydatetime(),
            amount=amount
        )
        db.add(div)

    db.commit()
    logger.info(f"Fetched data for {stock.ticker}: {len(history)} prices, {len(dividends)} dividends.")

def ingest_stocks(tickers: list):
    for ticker in tickers:
        stock = add_stock(ticker)
        fetch_stock_data(stock)