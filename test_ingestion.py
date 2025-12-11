# test_ingestion.py
from app.services.ingestion import ingest_stock_data
from app.services.fetch import fetch_stock_data


def main():
    tickers = ["KO"]
    try:
        # ingest_stock_data(tickers)
        # print("Ingestion completed successfully.")

        prices_df, dividends_series = fetch_stock_data(tickers[0])
        print("Fetched Prices:")
        print(prices_df.head())
        print("\nFetched Dividends:")
        print(dividends_series.head())
    except Exception as e:
        print(f"Ingestion failed: {e}")

if __name__ == "__main__":
    main()
