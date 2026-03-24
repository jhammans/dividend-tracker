"""
Unit tests for the holdings service (SchwabHoldingsParser + HoldingsImporter).

Uses the db_session and tmp_csv fixtures from conftest.py.
"""
import pytest
from decimal import Decimal
from datetime import date

from app.models import Stock, Account, Holding
from app.services.holdings import (
    SchwabHoldingsParser,
    HoldingsImporter,
    HoldingsImportError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_account(db, name="Test Schwab", broker="SCHWAB"):
    account = Account(account_name=name, broker=broker, account_type="TAXABLE")
    db.add(account)
    db.flush()
    return account


def make_stock(db, ticker, name=None):
    stock = Stock(ticker=ticker, name=name or ticker, asset_type="STOCK")
    db.add(stock)
    db.flush()
    return stock


# Schwab exports have 2 metadata rows before the real header
SCHWAB_META = [
    ["Positions for account: Test-1234", "", "", ""],
    ["", "", "", ""],
]
SCHWAB_HEADER = ["Symbol", "Qty (Quantity)", "Cost Basis", "Last Price"]


def schwab_rows(*holdings):
    """Build a minimal Schwab positions CSV (meta + header + data rows)."""
    return SCHWAB_META + [SCHWAB_HEADER] + list(holdings)


# ---------------------------------------------------------------------------
# SchwabHoldingsParser
# ---------------------------------------------------------------------------

class TestSchwabHoldingsParser:
    def test_parse_csv_happy_path(self, tmp_csv):
        rows = schwab_rows(
            ["AAPL", "10", "$1,500.00", "$155.00"],
            ["KO", "25", "$1,562.50", "$63.00"],
        )
        path = tmp_csv(rows)
        df = SchwabHoldingsParser.parse_csv(path)

        assert len(df) == 2
        assert list(df["Symbol"]) == ["AAPL", "KO"]
        assert df.iloc[0]["Qty (Quantity)"] == 10.0
        assert df.iloc[0]["Cost Basis"] == 1500.0

    def test_parse_csv_filters_cash_and_account_total_rows(self, tmp_csv):
        rows = schwab_rows(
            ["AAPL", "10", "$1,500.00", "$155.00"],
            ["Cash & Cash Investments", "500", "$500.00", ""],
            ["Account Total", "", "$2,000.00", ""],
        )
        path = tmp_csv(rows)
        df = SchwabHoldingsParser.parse_csv(path)

        assert len(df) == 1
        assert df.iloc[0]["Symbol"] == "AAPL"

    def test_parse_csv_strips_dollar_signs_and_commas(self, tmp_csv):
        rows = schwab_rows(
            ["SCHD", "100", "$10,250.75", "$102.50"],
        )
        path = tmp_csv(rows)
        df = SchwabHoldingsParser.parse_csv(path)

        assert df.iloc[0]["Cost Basis"] == 10250.75

    def test_parse_csv_missing_required_column_raises(self, tmp_csv):
        bad_rows = SCHWAB_META + [
            ["Symbol", "Last Price"],   # missing Qty (Quantity) and Cost Basis
            ["AAPL", "$155.00"],
        ]
        path = tmp_csv(bad_rows)
        with pytest.raises(HoldingsImportError, match="missing required columns"):
            SchwabHoldingsParser.parse_csv(path)

    def test_parse_csv_file_not_found_raises(self):
        with pytest.raises(HoldingsImportError, match="not found"):
            SchwabHoldingsParser.parse_csv("/nonexistent/path.csv")

    def test_parse_csv_drops_rows_with_nan_quantity(self, tmp_csv):
        rows = schwab_rows(
            ["AAPL", "10", "$1,500.00", "$155.00"],
            ["MSFT", "", "$0.00", "$400.00"],   # missing quantity
        )
        path = tmp_csv(rows)
        df = SchwabHoldingsParser.parse_csv(path)
        assert len(df) == 1


# ---------------------------------------------------------------------------
# HoldingsImporter
# ---------------------------------------------------------------------------

class TestHoldingsImporter:
    def test_creates_new_holding(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")

        rows = schwab_rows(["AAPL", "10", "$1,500.00", "$155.00"])
        path = tmp_csv(rows)

        importer = HoldingsImporter(db_session)
        results = importer.import_schwab_holdings(path, account)

        assert results["imported"] == 1
        assert results["updated"] == 0
        holding = db_session.query(Holding).first()
        assert holding.quantity == Decimal("10")
        assert holding.cost_basis == Decimal("1500")
        assert holding.cost_basis_source == "EXACT"

    def test_updates_existing_holding(self, db_session, tmp_csv):
        account = make_account(db_session)
        stock = make_stock(db_session, "AAPL")

        # Create an existing holding
        existing = Holding(
            account_id=account.id,
            stock_id=stock.id,
            quantity=Decimal("5"),
            cost_basis=Decimal("750"),
            cost_basis_source="EXACT",
            import_date=date(2024, 1, 1),
            has_complete_history=False,
        )
        db_session.add(existing)
        db_session.flush()

        rows = schwab_rows(["AAPL", "10", "$1,500.00", "$155.00"])
        path = tmp_csv(rows)

        importer = HoldingsImporter(db_session)
        results = importer.import_schwab_holdings(path, account)

        assert results["updated"] == 1
        assert results["imported"] == 0
        holding = db_session.query(Holding).first()
        assert holding.quantity == Decimal("10")
        assert holding.cost_basis == Decimal("1500")

    def test_skips_missing_stock(self, db_session, tmp_csv):
        account = make_account(db_session)
        # AAPL not in DB

        rows = schwab_rows(["AAPL", "10", "$1,500.00", "$155.00"])
        path = tmp_csv(rows)

        importer = HoldingsImporter(db_session)
        results = importer.import_schwab_holdings(path, account)

        assert results["failed"] == 1
        assert results["imported"] == 0
        assert "AAPL" in results["missing_stocks"]

    def test_imports_multiple_holdings(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")
        make_stock(db_session, "KO")

        rows = schwab_rows(
            ["AAPL", "10", "$1,500.00", "$155.00"],
            ["KO", "25", "$1,562.50", "$63.00"],
        )
        path = tmp_csv(rows)

        importer = HoldingsImporter(db_session)
        results = importer.import_schwab_holdings(path, account)

        assert results["imported"] == 2
        assert db_session.query(Holding).count() == 2

    def test_uses_provided_import_date(self, db_session, tmp_csv):
        account = make_account(db_session)
        make_stock(db_session, "AAPL")

        rows = schwab_rows(["AAPL", "10", "$1,500.00", "$155.00"])
        path = tmp_csv(rows)
        snapshot_date = date(2025, 3, 1)

        importer = HoldingsImporter(db_session)
        importer.import_schwab_holdings(path, account, import_date=snapshot_date)

        holding = db_session.query(Holding).first()
        assert holding.import_date == snapshot_date

    def test_error_on_bad_csv_returns_empty_results(self, db_session):
        account = make_account(db_session)

        importer = HoldingsImporter(db_session)
        results = importer.import_schwab_holdings("/nonexistent/file.csv", account)

        assert results["imported"] == 0
        assert len(results["errors"]) > 0
