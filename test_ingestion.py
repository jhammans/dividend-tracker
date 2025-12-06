# test_ingestion.py
from app.services.ingestion import ingest_stock_data
from app.db.session import SessionLocal

def main():
    db = SessionLocal()
    try:
        # Example tickers to test
        tickers = ["AAPL", "MSFT", "KO"]
        ingest_stocks(db, tickers)
        print("Ingestion completed successfully.")
    except Exception as e:
        print(f"Ingestion failed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
