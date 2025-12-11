# app/services/fetch.py
import yfinance as yf

def fetch_stock_data(ticker: str):
    """
    Fetch historical stock prices and dividends for a ticker.
    Returns:
        prices (pd.DataFrame): indexed by date with open, high, low, close, volume
        dividends (pd.Series): indexed by date with dividend values
    """
    stock = yf.Ticker(ticker)

    # Fetch price history
    prices = stock.history(period="max")[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    prices.index.name = 'Date'
    prices.columns = ['open', 'high', 'low', 'close', 'volume']

    # Fetch dividends
    dividends = stock.dividends.copy()
    dividends.index.name = 'Date'

    return prices, dividends
