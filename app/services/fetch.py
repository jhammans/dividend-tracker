# app/services/fetch.py
import yfinance as yf
import logging

logger = logging.getLogger(__name__)

class InvalidTickerError(Exception):
    """Raised when a ticker does not exist or has no data"""
    pass

def fetch_stock_data(ticker: str):
    """
    Fetch historical stock prices and dividends for a ticker.
    
    Raises:
        InvalidTickerError: If ticker doesn't exist or has no price data
    
    Returns:
        prices (pd.DataFrame): indexed by date with open, high, low, close, volume
        dividends (pd.Series): indexed by date with dividend values
    """
    try:
        stock = yf.Ticker(ticker)

        # Fetch price history
        history = stock.history(period="max")
        
        # Validate that we have data
        if history.empty:
            raise InvalidTickerError(f"No price data found for ticker '{ticker}' - ticker may be invalid or delisted")
        
        prices = history[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        prices.index.name = 'Date'
        prices.columns = ['open', 'high', 'low', 'close', 'volume']

        # Fetch dividends (may be empty, which is OK)
        dividends = stock.dividends.copy()
        dividends.index.name = 'Date'

        return prices, dividends
    
    except InvalidTickerError:
        raise
    except KeyError as e:
        raise InvalidTickerError(f"Failed to parse data for ticker '{ticker}': {e}")
    except Exception as e:
        raise InvalidTickerError(f"Unexpected error fetching '{ticker}': {e}")
