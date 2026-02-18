# test_ingestion.py
from app.services.ingestion import ingest_stock_data
from app.services.fetch import fetch_stock_data, InvalidTickerError


def main():
    tickers = ["KO", "AAPL", "INVALID_TICKER_XYZ", "MSFT"]
    try:
        print("=== Step 1: Fetching data ===")
        for ticker in tickers:
            try:
                prices_df, dividends_series = fetch_stock_data(ticker)
                print(f"\n{ticker}:")
                print(f"  Prices: {len(prices_df)} records")
                print(f"  Dividends: {len(dividends_series)} records")
                print(f"  Price range: {prices_df.index.min()} to {prices_df.index.max()}")
                print(f"  Latest price: {prices_df['close'].iloc[-1]:.2f}")
            except InvalidTickerError as e:
                print(f"\n{ticker}:")
                print(f"  ✗ Error: {e}")
        
        print("\n=== Step 2: Ingesting to database ===")
        ingest_stock_data(tickers)
        print("✓ Ingestion completed successfully!")
    except Exception as e:
        import traceback
        print(f"✗ Ingestion failed: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
