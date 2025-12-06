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
    last_updated = Column(DateTime, default=datetime.utcnow)

    prices = relationship('StockPrice', back_populates='stock', cascade='all, delete-orphan')
    dividends = relationship('Dividend', back_populates='stock', cascade='all, delete-orphan')
    holdings = relationship('Holding', back_populates='stock', cascade='all, delete-orphan')
    transactions = relationship('Transaction', back_populates='stock', cascade='all, delete-orphan')
    alerts = relationship('Alert', back_populates='stock', cascade='all, delete-orphan')
    ai_analyses = relationship('AIAnalysis', back_populates='stock', cascade='all, delete-orphan')

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
    date = Column(Date)

    stock = relationship('Stock', back_populates='dividends')

class Holding(Base):
    __tablename__ = 'holdings'
    __table_args__ = (
    UniqueConstraint('stock_id', name='uq_holdings_stock'),
    )

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='CASCADE'), nullable=False)
    quantity = Column(Numeric, nullable=False, default=0)
    cost_basis = Column(Numeric, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    stock = relationship('Stock', back_populates='holdings')

class Transaction(Base):
    __tablename__ = 'transactions'


    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id', ondelete='SET NULL'))
    type = Column(String(length=32), nullable=False) # buy, sell, drip, split, div_payment
    quantity = Column(Numeric)
    price = Column(Numeric)
    total = Column(Numeric)
    date = Column(Date)


    stock = relationship('Stock', back_populates='transactions')


class PortfolioMetric(Base):
    __tablename__ = 'portfolio_metrics'
    __table_args__ = (
    UniqueConstraint('date', name='uq_portfolio_metrics_date'),
    )


    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    total_value = Column(Numeric)
    total_dividends = Column(Numeric)
    forward_dividend_income = Column(Numeric)
    yield_on_cost = Column(Numeric)

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