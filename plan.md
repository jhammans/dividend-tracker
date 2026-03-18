# Plan: Dividend Tracker Phase 2 — Analytics & Dashboard

## TL;DR
Add a reporting/analytics layer to compute dividend income, portfolio metrics, and performance, then build a web dashboard to visualize it. Design the code to be extensible for future REST API and alerting features.

**Priority Order**: (1) Dividend tracking, (2) Performance metrics, (3) Portfolio visualization

## Steps

### Phase 1: Analytics & Reporting Layer (backend business logic)
1. Create `analytics.py` service to compute:
   - **Dividend income**: Total dividends received per account/stock, forward dividend income (estimated from holdings)
   - **Portfolio metrics**: Current value, cost basis, gain/loss (realized & unrealized), yield on cost
   - **Performance metrics**: Return %, dividend yield, allocation % by sector/stock
   - *Depends on*: Existing holdings, transactions, stock prices, dividend data

2. Create `portfolio_summary.py` service to aggregate:
   - Multi-account summaries (user likely has IRA + taxable accounts)
   - Time-series snapshots (portfolio value over time for charts)
   - Sharpe ratio, CAGR, or other standard metrics (optional but extensible)

3. Create database view/materialized table `portfolio_snapshots` to cache daily portfolio state
   - Enables efficient dashboard queries without recalculating on every load
   - Backfill historical data from transactions

### Phase 2: Web Dashboard (FastAPI + lightweight frontend)
1. Set up **FastAPI** server with endpoints:
   - `/api/portfolio/summary` — current portfolio state (value, allocation, yield)
   - `/api/portfolio/history` — time-series for charts
   - `/api/dividends/summary` — dividend income metrics
   - `/api/dividends/forecast` — forward dividend income
   - `/api/accounts/<account_id>/holdings` — holdings breakdown
   - *Depends on Phase 1*

2. Build **simple HTML/JS frontend** (no heavy framework yet):
   - Dashboard page with key metrics (total value, YTD dividend income, yield, allocation)
   - Dividend income chart (monthly/annual received vs. forecasted)
   - Portfolio allocation pie/bar charts
   - Holdings table (ticker, shares, cost, current value, gain/loss)
   - Single account view (can filter/switch accounts)
   - *Depends on Phase 2 API*

3. Serve frontend from FastAPI (static files + templating)

### Phase 3: Extensibility hooks (defer implementation, but structure code now)
- Create `alerts.py` skeleton (dispatch method, no implementation yet)
- Add polymorphic design to analytics (easy to add new metrics/queries)
- Document API structure so future REST clients can build on it

## Relevant files
- **New files to create**:
  - `app/services/analytics.py` — core dividend & metric calculations
  - `app/services/portfolio_summary.py` — multi-account aggregation & snapshots
  - `app/main.py` — FastAPI app entry point
  - `app/api/endpoints.py` — API routes for dashboard
  - `app/templates/` — HTML/JS dashboard
  - `alembic/versions/*.py` — migration for portfolio_snapshots table

- **Existing files to extend**:
  - `app/models.py` — add PortfolioSnapshot model
  - `requirements.txt` — add FastAPI, Jinja2 for templating
  - `app/db/session.py` — ensure FastAPI session management is in place

## Verification
1. **Analytics layer**:
   - Unit test `analytics.py` with sample holdings/transactions/prices
   - Verify dividend calculations match manual spot checks
   - Performance: confirm portfolio snapshots compute in <1s

2. **Dashboard**:
   - Load dashboard, verify homepage renders without errors
   - Check that all 4-5 key charts/tables populate with correct data
   - Manual check: dividend income matches import summary
   - Responsive on desktop (mobile can be later)

3. **End-to-end**:
   - Import real account data → compute metrics → view in dashboard
   - Verify numbers match Schwab statements (within rounding)

## Decisions (from user clarification)
- **Web framework**: FastAPI (lightweight, async, extensible, good for future REST API)
- **Frontend**: Plain HTML/JS + Chart.js (no React/Vue to keep complexity low; can upgrade later)
- **Database caching**: PortfolioSnapshot table (easier to backfill, cleaner to query)
- **Single account vs. multi-account**: Build multi-account support; show all aggregated by default with drill-down to individual accounts
- **Alerts/API**: Defer full implementation; create skeleton with extension points for Phase 3+
- **Dividend projection**: Use historical average (past 4 quarters) to smooth out one-time variations
- **Incomplete cost basis**: Show warning but still calculate metrics
- **Multi-currency**: Assume all USD for MVP (simplify now, add FX conversion later if needed)
- **Snapshot frequency**: Daily at market close (4 PM ET)
- **Missing price data**: Warn and skip that period (don't guess/forward-fill)
- **YTD definition**: Calendar year (Jan 1 → today)
- **DRIP cost basis**: Use dividend amount ÷ shares issued from transaction data
- **Sold positions**: Hide from holdings table (only show current positions with quantity > 0)
- **Account reconciliation**: Show warning if holdings don't match transaction-derived quantity (catch import gaps)
- **Dashboard view**: Aggregate all accounts by default with ability to filter/drill down
- **Performance attribution**: Show top gainers/losers by individual stock
- **Missing dividend history**: Skip from forward estimate (don't assume $0)
- **Sector allocation incomplete**: Show as 'Unknown' category in charts
- **Snapshot backfill**: Background job (external cron, not built-in scheduler)
- **Multi-user support**: Single-user only (simplify auth)
- **Manual refresh**: Allow "Refresh Now" button in UI (triggers snapshot + metrics update on-demand)
- **Metrics computation**: Pre-compute and cache in separate PortfolioMetrics table (not on-demand), updates with background job
- **Price data gaps**: Skip weekends/holidays (don't create snapshots, no forward-fill)
- **PortfolioSnapshot granularity**: Per-account + per-holding detail (store each stock's contribution to each account)
- **Reporting scope**: Dashboard only (CSV export deferred to Phase 3+)
- **Historical holdings reconstruction**: Reconstruct from transaction history for past snapshots (more accurate)
- **Cron schedule**: User configurable in settings
- **Dividend calculation with <4 quarters**: Use available data (min 1 quarter), add footnote explaining data used
- **Dividend dates**: Use pay-date for calculations (when cash received)
- **Incomplete cost basis**: Leave blank and exclude from gain/loss calculations
- **Lot selection for sales (realized gains)**: Use LIFO (Last In, First Out)
- **Incomplete transaction history warnings**: Show on dashboard + use imported holdings snapshot as fallback
- **Dashboard performance**: Add caching headers + loading indicators
- **New stock backfill**: Automatically backfill price history + snapshots when stock added

## Implementation Notes
- **Time zone handling**: Normalize all timestamps to UTC in DB, convert to user TZ in UI
- **Backfill window**: Past 12 months for MVP
- **Data validation**: Snapshots must validate (total value = sum of per-holding values)
- **Reconciliation threshold**: Warn if |derived_qty - imported_qty| > 0.01 shares
- **Historical price data**: Backfill prices for stocks before creating their snapshots
- **PortfolioMetrics table**: Denormalized for speed (avoid JOIN on every dashboard query)
- **Cost of capital / benchmarks**: Not in MVP, but ensure extensible (FK to benchmark table)

## Analytics Layer — Key Function Signatures

```python
# analytics.py
reconstruct_holdings_from_transactions(account_id, as_of_date)
  # uses LotTracker, reads transactions table, applies splits via ratio= kwarg

compute_holdings_value(account_id, date)
  # qty × latest stock_prices.close

compute_dividend_income(account_id, year)
  # sums DIVIDEND_PAYMENT transactions by ex_date

compute_forward_income(account_id)
  # 4-quarter avg from dividends table × qty
  # skip stocks with no history; return footnote (quarters used)

compute_yield_on_cost(account_id)
  # forward_income / total_cost_basis

compute_performance_attribution(account_id)
  # top gainers/losers by stock
```

## Key Data Contracts
- `dividends` table = forward projections source (yfinance dividend history)
- `transactions` table = source of truth for historical dividend income received
- `Dividend.frequency` values: `'Monthly'`, `'Quarterly'`, `'SemiAnnual'`, `'Annual'`
- `StockSplit.ratio`: e.g. `4.0` = 4:1 forward split, `0.5` = 1:2 reverse split
- `cost_basis_source='INCOMPLETE'`: exclude from gain/loss, show blank in UI
- Sold positions (qty=0): hide from dashboard holdings table
- Lot selection: LIFO for realized gains

## Phase Completion Status
- ✅ Phase 0: Pre-analytics bug fixes (6 fixes committed)
- ✅ Data backfills: dividends.frequency, stock_splits, stock metadata
- ✅ Pipeline hardening: 13 bugs fixed across ingestion + transactions
- ✅ Full ingest test: all 3 brokers (Schwab, JazzWealth, Robinhood) passing
- ✅ Refresh CLI command added
- ⬜ Phase 1: Analytics layer (`app/services/analytics.py`)
- ⬜ Phase 2: FastAPI + Dashboard
- ⬜ Phase 3: Cron, alerts skeleton
