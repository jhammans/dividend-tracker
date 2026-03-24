"""
Unit tests for TransactionImporter — exercises the DB write layer.

Uses the db_session fixture (SQLite in-memory) and tmp_csv fixture from conftest.py.
Stock and Account rows are created as needed per test.
"""
import pytest
from decimal import Decimal
from datetime import date

from app.models import Stock, Account, Transaction
from app.services.transactions import TransactionImporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_account(db, name="Test Account", broker="SCHWAB"):
    account = Account(account_name=name, broker=broker, account_type="TAXABLE")
    db.add(account)
    db.flush()
    return account


def make_stock(db, ticker="AAPL", name="Apple Inc.", asset_type="STOCK"):
    stock = Stock(ticker=ticker, name=name, asset_type=asset_type)
    db.add(stock)
    db.flush()
    return stock


SCHWAB_HEADER = ["Date", "Type", "Symbol", "Quantity", "Price", "Amount"]
RH_HEADER = ["Activity Date", "Instrument", "Trans Code", "Quantity", "Price", "Amount"]
JAZZ_HEADER = ["Date", "Symbol", "Transaction", "Type", "price", "quanitty", "amount"]


# ---------------------------------------------------------------------------
# Schwab importer
# ---------------------------------------------------------------------------

class TestImportSchwabTransactions:
    def test_imports_buy_transaction(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")

        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_schwab_transactions(path, account)

        assert results["imported_count"] == 1
        assert results["failed_count"] == 0
        txn = db_session.query(Transaction).first()
        assert txn.type == "BUY"
        assert txn.quantity == Decimal("10")
        assert txn.total == Decimal("1500")

    def test_skips_unknown_transaction_type(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")

        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "UNKNOWN_TYPE", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_schwab_transactions(path, account)

        assert results["imported_count"] == 0
        assert results["skipped_count"] == 1

    def test_skips_missing_stock(self, db_session, tmp_csv):
        account = make_account(db_session)
        # AAPL not added to DB

        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_schwab_transactions(path, account)

        assert results["imported_count"] == 0
        assert results["failed_count"] == 1

    def test_skips_non_security_types(self, db_session, tmp_csv):
        account = make_account(db_session)

        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "CASH IN", "", "", "", "$500.00"],   # DEPOSIT
            ["01/16/2025", "BANK INTEREST", "", "", "", "$1.00"],  # INTEREST
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_schwab_transactions(path, account)

        assert results["imported_count"] == 0
        assert results["skipped_count"] == 2

    def test_deduplicates_by_broker_transaction_id(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")

        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        importer.import_schwab_transactions(path, account)  # first import
        results = importer.import_schwab_transactions(path, account)  # second import

        assert results["duplicates_skipped"] == 1
        assert db_session.query(Transaction).count() == 1

    def test_year_filter(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")

        rows = [
            SCHWAB_HEADER,
            ["01/15/2024", "BUY", "AAPL", "5", "$140.00", "$700.00"],
            ["01/15/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_schwab_transactions(path, account, year=2025)

        assert results["imported_count"] == 1
        txn = db_session.query(Transaction).first()
        assert txn.date == date(2025, 1, 15)

    def test_imports_dividend_payment(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "KO")

        rows = [
            SCHWAB_HEADER,
            ["02/01/2025", "DIVIDEND", "KO", "", "", "$25.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_schwab_transactions(path, account)

        assert results["imported_count"] == 1
        txn = db_session.query(Transaction).first()
        assert txn.type == "DIVIDEND_PAYMENT"
        assert txn.total == Decimal("25")


# ---------------------------------------------------------------------------
# Robinhood importer
# ---------------------------------------------------------------------------

class TestImportRobinhoodTransactions:
    def test_imports_buy_transaction(self, db_session, tmp_csv):
        account = make_account(db_session, broker="ROBINHOOD")
        make_stock(db_session, "AAPL")

        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "BUY", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_robinhood_transactions(path, account)

        assert results["imported_count"] == 1
        txn = db_session.query(Transaction).first()
        assert txn.type == "BUY"
        assert txn.quantity == Decimal("10")

    def test_skips_deposit_type(self, db_session, tmp_csv):
        account = make_account(db_session, broker="ROBINHOOD")

        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "ACH", "", "", "$500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_robinhood_transactions(path, account)

        assert results["imported_count"] == 0
        assert results["skipped_count"] == 1

    def test_sell_inferred_from_negative_quantity(self, db_session, tmp_csv):
        account = make_account(db_session, broker="ROBINHOOD")
        make_stock(db_session, "AAPL")

        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "SELL", "-10", "$150.00", "($1,500.00)"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_robinhood_transactions(path, account)

        assert results["imported_count"] == 1
        txn = db_session.query(Transaction).first()
        assert txn.type == "SELL"
        assert txn.quantity == Decimal("10")  # stored as absolute value

    def test_deduplicates_transaction(self, db_session, tmp_csv):
        account = make_account(db_session, broker="ROBINHOOD")
        make_stock(db_session, "AAPL")

        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "BUY", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        importer.import_robinhood_transactions(path, account)
        results = importer.import_robinhood_transactions(path, account)

        assert results["duplicates_skipped"] == 1
        assert db_session.query(Transaction).count() == 1

    def test_skips_missing_stock(self, db_session, tmp_csv):
        account = make_account(db_session, broker="ROBINHOOD")
        # stock not in DB

        rows = [
            RH_HEADER,
            ["1/15/2025", "NVDA", "BUY", "5", "$800.00", "$4,000.00"],
        ]
        path = tmp_csv(rows)

        importer = TransactionImporter(db_session)
        results = importer.import_robinhood_transactions(path, account)

        assert results["failed_count"] == 1
        assert results["imported_count"] == 0
