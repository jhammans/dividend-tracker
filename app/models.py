from datetime import datetime
from sqlalchemy import (
Column,
Integer,
String,
Date,
DateTime,
Numeric,
BigInteger,
Boolean,
ForeignKey,
UniqueConstraint,
Index,
text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Stock(Base):
    __tablename__ = 'stocks'

    id = Column(Integer, primary_key=True)
    ticker = Column(String(length=32), unique=True, nullable=False, index=True)
    name = Column(String(length=256))
    exchange = Column(String(length=32))
    sector = Column(String(length=128))
    industry = Column(String(length=128))
    currency = Column(String(length=8), default='USD')
    asset_type = Column(String(length=32), default='EQUITY')  # EQUITY, ETF, MUTUALFUND
    last_updated = Column(DateTime, default=datetime.utcnow)

    prices = relationship('StockPrice', back_populates='stock', cascade='all, delete-orphan')
    dividends = relationship('Dividend', back_populates='stock', cascade='all, delete-orphan')
    splits = relationship('StockSplit', back_populates='stock', cascade='all, delete-orphan')
    holdings = relationship('Holding', back_populates='stock', cascade='all, delete-orphan')
    transactions = relationship('Transaction', back_populates='stock', cascade='all, delete-orphan')
    alerts = relationship('Alert', back_populates='stock', cascade='all, delete-orphan')
    ai_analyses = relationship('AIAnalysis', back_populates='stock', cascade='all, delete-orphan')

class Account(Base):
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True)
    account_name = Column(String(length=256), nullable=False)  # e.g., "IRA - John", "Schwab Taxable"
    broker = Column(String(length=32), nullable=False)  # SCHWAB, FIDELITY, ROBINHOOD
    account_type = Column(String(length=32), nullable=False)  # IRA, ROTH_IRA, TAXABLE, 401K, etc.
    account_number = Column(String(length=64))  # Last 4 digits or full account number
    created_at = Column(DateTime, default=datetime.utcnow)
    
    holdings = relationship('Holding', back_populates='account', cascade='all, delete-orphan')
    transactions = relationship('Transaction', back_populates='account', cascade='all, delete-orphan')
    portfolio_metrics = relationship('PortfolioMetric', back_populates='account', cascade='all, delete-orphan')

class StockPrice(Base):
    __tablename__ = 'stock_prices'
    __table_args__ = (
    UniqueConstraint('stock_id', 'date', name='uq_stock_date'),
    Index('ix_stock_prices_stock_id_date', 'stock_id', 'date'),
    )

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Numeric)
    high = Column(Numeric)
    low = Column(Numeric)
    close = Column(Numeric)
    volume = Column(BigInteger)

    stock = relationship('Stock', back_populates='prices')

class Dividend(Base):
    __tablename__ = 'dividends'
    __table_args__ = (
    UniqueConstraint('stock_id', 'ex_date', name='uq_dividend_stock_exdate'),
    Index('ix_dividends_stock_id_ex_date', 'stock_id', 'ex_date'),
    )

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'), nullable=False)
    ex_date = Column(Date)
    pay_date = Column(Date)
    record_date = Column(Date)
    declared_date = Column(Date)
    amount = Column(Numeric)
    frequency = Column(String(length=32))
    provider_date = Column(Date)  # Raw date as returned by the data provider (e.g. yfinance)

    stock = relationship('Stock', back_populates='dividends')


class StockSplit(Base):
    __tablename__ = 'stock_splits'
    __table_args__ = (
        UniqueConstraint('stock_id', 'date', name='uq_stock_split_stock_date'),
        Index('ix_stock_splits_stock_id_date', 'stock_id', 'date'),
    )

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'), nullable=False)
    date = Column(Date, nullable=False)
    ratio = Column(Numeric, nullable=False)  # e.g. 4.0 for a 4:1 forward split, 0.5 for a 1:2 reverse split
    source = Column(String(length=32), default='YFINANCE')

    stock = relationship('Stock', back_populates='splits')


class Holding(Base):
    __tablename__ = 'holdings'
    __table_args__ = (
    UniqueConstraint('account_id', 'stock_id', name='uq_holding_account_stock'),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id', ondelete='CASCADE'), nullable=False)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'), nullable=False)
    quantity = Column(Numeric, nullable=False, default=0)
    cost_basis = Column(Numeric, nullable=False, default=0)
    cost_basis_source = Column(String(length=32), default='INCOMPLETE')  # EXACT, ESTIMATED, INCOMPLETE
    import_date = Column(Date)  # When this holding snapshot was imported
    has_complete_history = Column(Boolean, default=False)  # True if from account inception
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship('Account', back_populates='holdings')
    stock = relationship('Stock', back_populates='holdings')

class Transaction(Base):
    __tablename__ = 'transactions'

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id', ondelete='CASCADE'), nullable=False)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='SET NULL'))
    type = Column(String(length=32), nullable=False)  # BUY, SELL, DRIP, DIVIDEND_PAYMENT, SPLIT, TRANSFER
    quantity = Column(Numeric)
    price = Column(Numeric)
    total = Column(Numeric)
    commission = Column(Numeric, default=0)
    fees = Column(Numeric, default=0)
    date = Column(Date)
    broker_transaction_id = Column(String(length=256))  # Unique ID from broker CSV (e.g., Schwab transaction ID)
    is_estimated = Column(Boolean, default=False)  # True if reconstructed from holdings, not from broker CSV
    source = Column(String(length=32), default='BROKER_CSV')  # BROKER_CSV, USER_INPUT, CALCULATED

    account = relationship('Account', back_populates='transactions')
    stock = relationship('Stock', back_populates='transactions')


class PortfolioMetric(Base):
    __tablename__ = 'portfolio_metrics'
    __table_args__ = (
        # Unique per account+date for per-account rows
        Index('uq_portfolio_metrics_account_date', 'account_id', 'date', unique=True,
              postgresql_where=text('account_id IS NOT NULL')),
        # Unique per date for aggregate rows (account_id IS NULL = all-accounts total)
        Index('uq_portfolio_metrics_agg_date', 'date', unique=True,
              postgresql_where=text('account_id IS NULL')),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id', ondelete='CASCADE'), nullable=True)  # NULL = all-accounts aggregate
    date = Column(Date, nullable=False)
    total_value = Column(Numeric)
    total_cost_basis = Column(Numeric)
    total_unrealized_gain_loss = Column(Numeric)
    total_dividends = Column(Numeric)          # YTD realized dividend income (from transactions)
    forward_dividend_income = Column(Numeric)  # Projected annual income (from dividends table)
    yield_on_cost = Column(Numeric)            # forward_dividend_income / total_cost_basis
    dividend_yield = Column(Numeric)           # forward_dividend_income / total_value

    account = relationship('Account', back_populates='portfolio_metrics')

class Alert(Base):
    __tablename__ = 'alerts'

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'))
    type = Column(String(length=64))
    message = Column(String(length=1024))
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved = Column(Boolean, default=False)

    stock = relationship('Stock', back_populates='alerts')


class AIAnalysis(Base):
    __tablename__ = 'ai_analysis'

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'))
    analysis_type = Column(String(length=128))
    content = Column(String) # JSON stored as text; migration later can convert to JSONB if desired
    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship('Stock', back_populates='ai_analyses')