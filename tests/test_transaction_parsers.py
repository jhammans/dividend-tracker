"""
Unit tests for transaction CSV parsers (Schwab, JazzWealth, Robinhood).

These tests exercise only the static parser methods — no database required.
The TransactionImporter DB layer is covered in test_transaction_importer.py.
"""
import pytest
from datetime import date

from app.services.transactions import (
    SchwabTransactionParser,
    JazzWealthTransactionParser,
    RobinhoodTransactionParser,
    TransactionImportError,
)


# ---------------------------------------------------------------------------
# SchwabTransactionParser
# ---------------------------------------------------------------------------

SCHWAB_HEADER = ["Date", "Type", "Symbol", "Quantity", "Price", "Amount"]


class TestSchwabParser:
    def test_parse_csv_happy_path(self, tmp_csv):
        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
            ["02/01/2025", "DIVIDEND", "KO", "", "", "$25.00"],
        ]
        path = tmp_csv(rows)
        df = SchwabTransactionParser.parse_csv(path)

        assert len(df) == 2
        assert df.iloc[0]["Symbol"] == "AAPL"
        assert df.iloc[0]["Date"] == date(2025, 1, 15)
        assert df.iloc[0]["Quantity"] == 10.0
        assert df.iloc[0]["Price"] == 150.0
        assert df.iloc[0]["Amount"] == 1500.0

    def test_parse_csv_action_column_renamed_to_type(self, tmp_csv):
        rows = [
            ["Date", "Action", "Symbol", "Quantity", "Price", "Amount"],
            ["03/10/2025", "SELL", "MSFT", "5", "$400.00", "$2,000.00"],
        ]
        path = tmp_csv(rows)
        df = SchwabTransactionParser.parse_csv(path)
        assert "Type" in df.columns
        assert "Action" not in df.columns

    def test_parse_csv_as_of_date_stripped(self, tmp_csv):
        rows = [
            SCHWAB_HEADER,
            ["02/17/2025 as of 02/15/2025", "DIVIDEND", "VYM", "", "", "$10.00"],
        ]
        path = tmp_csv(rows)
        df = SchwabTransactionParser.parse_csv(path)
        assert df.iloc[0]["Date"] == date(2025, 2, 17)

    def test_parse_csv_missing_required_column_raises(self, tmp_csv):
        # Missing 'Amount'
        rows = [
            ["Date", "Type", "Symbol", "Quantity", "Price"],
            ["01/01/2025", "BUY", "AAPL", "10", "$150.00"],
        ]
        path = tmp_csv(rows)
        with pytest.raises(TransactionImportError, match="missing required columns"):
            SchwabTransactionParser.parse_csv(path)

    def test_parse_csv_missing_type_action_column_raises(self, tmp_csv):
        rows = [
            ["Date", "Symbol", "Quantity", "Price", "Amount"],
            ["01/01/2025", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)
        with pytest.raises(TransactionImportError, match="'Type' or 'Action'"):
            SchwabTransactionParser.parse_csv(path)

    def test_parse_csv_file_not_found_raises(self):
        with pytest.raises(TransactionImportError, match="not found"):
            SchwabTransactionParser.parse_csv("/nonexistent/path.csv")

    def test_parse_csv_drops_rows_with_missing_amount(self, tmp_csv):
        rows = [
            SCHWAB_HEADER,
            ["01/01/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
            ["01/02/2025", "BUY", "MSFT", "5", "$300.00", ""],  # no amount
        ]
        path = tmp_csv(rows)
        df = SchwabTransactionParser.parse_csv(path)
        assert len(df) == 1
        assert df.iloc[0]["Symbol"] == "AAPL"

    @pytest.mark.parametrize("schwab_type,expected", [
        ("BUY", "BUY"),
        ("SELL", "SELL"),
        ("DIVIDEND", "DIVIDEND_PAYMENT"),
        ("CASH DIVIDEND", "DIVIDEND_PAYMENT"),
        ("REINVEST DIVIDEND", "DRIP"),
        ("STOCK SPLIT", "SPLIT"),
        ("buy", "BUY"),  # case-insensitive
    ])
    def test_map_transaction_type(self, schwab_type, expected):
        assert SchwabTransactionParser.map_transaction_type(schwab_type) == expected

    def test_map_transaction_type_unknown_returns_none(self):
        assert SchwabTransactionParser.map_transaction_type("UNKNOWN_OP") is None

    def test_create_broker_transaction_id_format(self, tmp_csv):
        rows = [
            SCHWAB_HEADER,
            ["01/15/2025", "BUY", "AAPL", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)
        df = SchwabTransactionParser.parse_csv(path)
        txn_id = SchwabTransactionParser.create_broker_transaction_id(df.iloc[0])
        assert txn_id.startswith("SCHWAB_20250115_BUY_AAPL_")


# ---------------------------------------------------------------------------
# JazzWealthTransactionParser
# ---------------------------------------------------------------------------

JAZZ_HEADER = ["Date", "Symbol", "Transaction", "Type", "price", "quanitty", "amount"]


class TestJazzWealthParser:
    def test_parse_csv_happy_path(self, tmp_csv):
        rows = [
            JAZZ_HEADER,
            ["01/15/25", "AAPL", "WINDOW TRADE", "BUY", "$150.00", "10", "$1,500.00"],
            ["02/01/25", "KO", "DIVIDEND", "DIVIDEND", "$0.00", "", "$25.00"],
        ]
        path = tmp_csv(rows)
        df = JazzWealthTransactionParser.parse_csv(path)
        assert len(df) == 2
        assert df.iloc[0]["Symbol"] == "AAPL"
        assert df.iloc[0]["Date"] == date(2025, 1, 15)
        assert df.iloc[0]["price"] == 150.0
        assert df.iloc[0]["quantity"] == 10.0

    def test_parse_csv_quanitty_typo_handled(self, tmp_csv):
        rows = [
            JAZZ_HEADER,
            ["01/15/25", "AAPL", "BUY", "BUY", "$150.00", "10", "$1,500.00"],
        ]
        path = tmp_csv(rows)
        df = JazzWealthTransactionParser.parse_csv(path)
        assert "quantity" in df.columns
        assert "quanitty" not in df.columns

    def test_parse_csv_negative_amount_parentheses(self, tmp_csv):
        rows = [
            JAZZ_HEADER,
            ["01/15/25", "AAPL", "SELL", "SELL", "$150.00", "-10", "($1,500.00)"],
        ]
        path = tmp_csv(rows)
        df = JazzWealthTransactionParser.parse_csv(path)
        assert df.iloc[0]["amount"] == -1500.0

    def test_parse_csv_drops_missing_date_or_amount(self, tmp_csv):
        rows = [
            JAZZ_HEADER,
            ["01/15/25", "AAPL", "BUY", "BUY", "$150.00", "10", "$1,500.00"],
            ["", "MSFT", "BUY", "BUY", "$300.00", "5", "$1,500.00"],  # no date
        ]
        path = tmp_csv(rows)
        df = JazzWealthTransactionParser.parse_csv(path)
        assert len(df) == 1

    def test_parse_csv_file_not_found_raises(self):
        with pytest.raises(TransactionImportError, match="not found"):
            JazzWealthTransactionParser.parse_csv("/nonexistent/path.csv")

    def test_parse_csv_missing_required_column_raises(self, tmp_csv):
        rows = [
            ["Date", "Transaction", "Type", "price", "quanitty", "amount"],  # no Symbol
            ["01/15/25", "BUY", "BUY", "$150.00", "10", "$1,500.00"],
        ]
        path = tmp_csv(rows)
        with pytest.raises(TransactionImportError, match="missing required column"):
            JazzWealthTransactionParser.parse_csv(path)

    @pytest.mark.parametrize("jazz_type,expected", [
        ("BUY", "BUY"),
        ("REINVESTMENTS", "DRIP"),
        ("DIVIDEND", "DIVIDEND_PAYMENT"),
        ("DEPOSITS", "DEPOSIT"),
        ("WITHDRAWAL", "WITHDRAWAL"),
        ("INTEREST", "INTEREST"),
    ])
    def test_map_transaction_type(self, jazz_type, expected):
        assert JazzWealthTransactionParser.map_transaction_type(jazz_type) == expected

    def test_map_window_trade_positive_qty_is_buy(self):
        result = JazzWealthTransactionParser.map_transaction_type("WINDOW TRADE", qty_type="BUY")
        assert result == "BUY"

    def test_map_window_trade_negative_qty_is_sell(self):
        result = JazzWealthTransactionParser.map_transaction_type("WINDOW TRADE", qty_type="SELL")
        assert result == "SELL"

    def test_is_non_security_cash_symbol(self):
        assert JazzWealthTransactionParser.is_non_security_transaction("DP.CASH", "DEPOSIT") is True

    def test_is_non_security_empty_symbol(self):
        assert JazzWealthTransactionParser.is_non_security_transaction("", "BUY") is True

    def test_is_non_security_equity_buy_is_false(self):
        assert JazzWealthTransactionParser.is_non_security_transaction("AAPL", "BUY") is False

    def test_infer_type_negative_quantity_is_sell(self):
        result = JazzWealthTransactionParser.infer_transaction_type_from_quantity(-5.0, "TRADE")
        assert result == "SELL"

    def test_infer_type_positive_quantity_is_buy(self):
        result = JazzWealthTransactionParser.infer_transaction_type_from_quantity(10.0, "TRADE")
        assert result == "BUY"


# ---------------------------------------------------------------------------
# RobinhoodTransactionParser
# ---------------------------------------------------------------------------

RH_HEADER = ["Activity Date", "Instrument", "Trans Code", "Quantity", "Price", "Amount"]


class TestRobinhoodParser:
    def test_parse_csv_happy_path(self, tmp_csv):
        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "BUY", "10", "$150.00", "$1,500.00"],
            ["2/1/2025", "KO", "CDIV", "", "$0.00", "$25.00"],
        ]
        path = tmp_csv(rows)
        df = RobinhoodTransactionParser.parse_csv(path)
        assert len(df) == 2
        assert df.iloc[0]["Instrument"] == "AAPL"
        assert df.iloc[0]["Date"] if "Date" in df.columns else df.iloc[0]["Activity Date"] == date(2025, 1, 15)
        assert df.iloc[0]["Price"] == 150.0
        assert df.iloc[0]["Amount"] == 1500.0

    def test_parse_csv_negative_amount_parentheses(self, tmp_csv):
        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "SELL", "-10", "$150.00", "($1,500.00)"],
        ]
        path = tmp_csv(rows)
        df = RobinhoodTransactionParser.parse_csv(path)
        assert df.iloc[0]["Amount"] == -1500.0

    def test_parse_csv_missing_required_column_raises(self, tmp_csv):
        rows = [
            ["Activity Date", "Instrument", "Trans Code", "Quantity", "Price"],  # missing Amount
            ["1/15/2025", "AAPL", "BUY", "10", "$150.00"],
        ]
        path = tmp_csv(rows)
        with pytest.raises(TransactionImportError, match="missing required columns"):
            RobinhoodTransactionParser.parse_csv(path)

    def test_parse_csv_file_not_found_raises(self):
        with pytest.raises(TransactionImportError, match="not found"):
            RobinhoodTransactionParser.parse_csv("/nonexistent/path.csv")

    @pytest.mark.parametrize("code,expected", [
        ("BUY", "BUY"),
        ("SELL", "SELL"),
        ("CDIV", "DIVIDEND_PAYMENT"),
        ("RDIV", "DRIP"),
        ("ACH", "DEPOSIT"),
        ("ACHA", "WITHDRAWAL"),
        ("DIV", "DIVIDEND_PAYMENT"),
        ("INT", "INTEREST"),
    ])
    def test_map_transaction_type(self, code, expected):
        assert RobinhoodTransactionParser.map_transaction_type(code) == expected

    def test_map_transaction_type_unknown_returns_none(self):
        assert RobinhoodTransactionParser.map_transaction_type("UNKNOWN") is None

    def test_create_broker_transaction_id_format(self, tmp_csv):
        rows = [
            RH_HEADER,
            ["1/15/2025", "AAPL", "BUY", "10", "$150.00", "$1,500.00"],
        ]
        path = tmp_csv(rows)
        df = RobinhoodTransactionParser.parse_csv(path)
        txn_id = RobinhoodTransactionParser.create_broker_transaction_id(df.iloc[0])
        assert txn_id.startswith("ROBINHOOD_20250115_BUY_AAPL_")
