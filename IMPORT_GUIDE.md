# Broker Data Import Guide

## Quick Start

Use the `import_pipeline.py` CLI tool to import your broker data:

> **Currently supported**: Schwab, JazzWealth/Goldman Sachs, Robinhood. Use `--broker` to select the parser.

```bash
# First, list available accounts
python import_pipeline.py accounts

# Then run the full pipeline
python import_pipeline.py full \
  ~/Downloads/Schwab_Positions_XXX612_20260226.csv \
  ~/Downloads/Schwab_Transactions_XXX612_20260226.csv \
  2
```

Replace the CSV paths with your actual files and `2` with your account ID from the accounts list.

---

## Pipeline Commands

### 1. List Accounts

```bash
python import_pipeline.py accounts
```

Shows all accounts in the database with their IDs. Use the ID for other commands.

### 2. Bootstrap Tickers (Optional)

Scans a CSV for unique ticker symbols and ensures they all exist in the database:

```bash
python import_pipeline.py bootstrap ~/Downloads/my_holdings.csv
```

The full pipeline includes bootstrap automatically, so this is only needed for diagnostics.

### 3. Import Holdings Only

Load a position snapshot (as of a specific date) with cost basis:

```bash
python import_pipeline.py holdings ~/Downloads/Schwab_Positions.csv 2
```

**CSV Format**: Requires `Symbol`, `Qty (Quantity)`, `Cost Basis` columns

### 4. Import Transactions Only

Load transaction history (buys, sells, dividends):

```bash
# Schwab (default)
python import_pipeline.py transactions ~/Downloads/Schwab_Transactions.csv 2

# JazzWealth / Goldman Sachs
python import_pipeline.py transactions ~/Downloads/GS_Transactions.csv 3 --broker GOLDMAN_SACHS

# Robinhood
python import_pipeline.py transactions ~/Downloads/Robinhood_Transactions.csv 4 --broker ROBINHOOD
```

**CSV Format**: Requires `Date`, `Type`, `Symbol`, `Quantity`, `Price`, `Amount` columns

### 5. Full Pipeline (Recommended)

Run all steps in sequence: bootstrap → holdings → transactions

```bash
python import_pipeline.py full \
  ~/Downloads/Schwab_Positions.csv \
  ~/Downloads/Schwab_Transactions.csv \
  2
```

### 6. Refresh yfinance Data

Re-fetch prices, dividends, splits, and metadata from yfinance for every stock in the database. Run this periodically to keep data current.

```bash
# Refresh all stocks
python import_pipeline.py refresh

# Refresh specific tickers only
python import_pipeline.py refresh --tickers AAPL SCHD VTI
```

Any stocks added via future `bootstrap`/`holdings`/`full` runs are automatically included in the next `refresh`.

---

## How to Export from Schwab

### Positions/Holdings Export

1. Log into Schwab.com
2. **Accounts → Account Activity → Positions**
3. Click **Download**
4. Select **CSV** format
5. Save file

Expected columns: `Symbol`, `Qty (Quantity)`, `Cost Basis`

### Transactions Export

1. Log into Schwab.com
2. **Accounts → Account Activity → Transactions**
3. Select date range (**2026-01-01 to 2026-12-31**)
4. Click **Download**
5. Select **CSV** format
6. Save file

Expected columns: `Date`, `Type`, `Symbol`, `Quantity`, `Price`, `Amount`

---

## How It Works

The pipeline follows this flow:

1. **Bootstrap** - Extracts all unique tickers from both CSVs and ensures they exist in the database
   - Missing tickers are fetched from yfinance automatically
   - Skips tickers that are already in the database

2. **Holdings Import** - Loads your position snapshot
   - Creates records in the `Holding` table
   - Tracks cost basis source (`EXACT` from broker CSV)
   - Records import date for audit trail

3. **Transactions Import** - Loads your transaction history
   - Filters for BUY/SELL transactions only
   - Skips dividends, interest, capital gains (non-trading events)
   - Detects and skips duplicate transactions
   - Creates records in the `Transaction` table with type, quantity, date, price

---

## What Gets Imported

### Holdings Table

For each position in your holdings CSV:
- Symbol (ticker)
- Quantity held
- Cost per share
- Total cost basis
- Import date (when snapshot was taken)
- Cost basis source (`EXACT` - from broker)

### Transactions Table

For each buy/sell transaction:
- Type (`BUY` or `SELL`)
- Symbol
- Quantity
- Price per share
- Total amount
- Transaction date
- Unique broker transaction ID (for duplicate detection)
- Source (`BROKER_CSV`)

### Skipped Records

The importer skips rows that don't map to a security:
- Bank interest, wire transfers, ACH deposits
- Capital gains distributions (non-security entries)
- Advisory fees

Dividend payments (`DIVIDEND_PAYMENT`), DRIP reinvestments, and buy/sell trades are all imported as transactions.

---

## Duplicate Detection

Every transaction is checked for duplicates using:

1. **Primary**: `broker_transaction_id` - Unique ID combining date, type, symbol, quantity, price
2. **Fallback**: Date + Stock + Amount - Catches variations

If a duplicate is found, the transaction is skipped and logged.

---

## Example Usage

```bash
# Activate virtual environment
source .dividend_track_venv/bin/activate

# List accounts to find your ID
python import_pipeline.py accounts
# Output:
#   Available accounts:
#   ID 1: Schwab Taxable Account (SCHWAB - TAXABLE)
#   ID 2: Schwab Contributory IRA (SCHWAB - IRA)

# Run full pipeline
python import_pipeline.py full \
  ~/Downloads/Schwab_Positions_XXXXX_20260226.csv \
  ~/Downloads/Schwab_Transactions_XXXXX_20260226.csv \
  2

# Typical output:
# ======================================================================
# [Step 1/3] Bootstrap tickers from both CSVs...
# Holdings CSV: 15 tickers verified
# Transactions CSV: 18 tickers verified
#
# [Step 2/3] Import holdings snapshot...
# ✓ Imported 15 holdings
#
# [Step 3/3] Import transactions...
# ✓ Imported 8 transactions
# ✓ Skipped 53 non-trade events
#
# ======================================================================
# ✓ FULL PIPELINE COMPLETE
# ======================================================================
# Account: Schwab Contributory IRA (SCHWAB)
# Holdings imported: 15
# Transactions imported: 8
```

---

## Troubleshooting

### "File not found"

```
Error: File not found: ~/Downloads/my_file.csv
```

Make sure the path is correct. Use the full path without `~` if it doesn't work:

```bash
python import_pipeline.py full \
  /Users/jhammans/Downloads/Schwab_Positions.csv \
  /Users/jhammans/Downloads/Schwab_Transactions.csv \
  2
```

### "Account ID not found"

```
Error: Account ID 5 not found in database
```

Run `python import_pipeline.py accounts` to see valid account IDs.

### CSV Column Not Found

If bootstrap can't find the ticker column, the file may have a different structure. Check that your CSV has one of these column names:
- `Symbol`, `Ticker`, `Security`, or `Instrument`

### Duplicate Transactions Skipped

This is expected behavior. The importer uses the `broker_transaction_id` to prevent duplicate imports if you run the pipeline twice.

---

## Related Files

- **app/services/bootstrap.py** - Ticker bootstrap service
- **app/services/holdings.py** - Holdings import service
- **app/services/transactions.py** - Transaction import service (Schwab, JazzWealth, Robinhood)
- **app/services/ingestion.py** - yfinance data ingestion (prices, dividends, splits, metadata)
- **app/services/splits.py** - Stock split backfill utility
- **app/models.py** - Database models (Stock, Holding, Transaction, Account, StockSplit)
