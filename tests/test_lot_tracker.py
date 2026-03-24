"""Unit tests for LotTracker — run with: pytest test_lot_tracker.py"""
from decimal import Decimal
from datetime import date
from app.services.lot_tracker import LotTracker


def test_lifo_basic():
    t = LotTracker('AAPL')
    t.process('BUY', date(2023, 1, 1), Decimal('100'), Decimal('10'))  # lot1: 100 @ 10
    t.process('BUY', date(2024, 1, 1), Decimal('50'),  Decimal('20'))  # lot2: 50 @ 20
    t.process('SELL', date(2024, 6, 1), Decimal('30'), None)            # LIFO: removes 30 from lot2

    assert t.total_quantity() == Decimal('120'), f'qty={t.total_quantity()}'
    # Remaining: 100 shares @ 10 + 20 shares @ 20 = 1400
    assert t.total_cost_basis() == Decimal('1400'), f'cost={t.total_cost_basis()}'
    assert t.has_complete_history


def test_split_2for1():
    t = LotTracker('TSLA')
    t.process('BUY',   date(2023, 1, 1), Decimal('100'), Decimal('200'))  # 100 @ 200, total=20000
    t.process('SPLIT', date(2024, 1, 1), Decimal('2'),   None)             # ratio=2 (stock_splits table)

    assert t.total_quantity() == Decimal('200'), f'qty={t.total_quantity()}'
    # Total cost basis must be preserved after split
    assert abs(t.total_cost_basis() - Decimal('20000')) < Decimal('0.01'), f'cost={t.total_cost_basis()}'
    assert t.lots()[0].cost_per_share == Decimal('100.000000'), f'cps={t.lots()[0].cost_per_share}'


def test_reverse_split():
    t = LotTracker('GME')
    t.process('BUY',   date(2023, 1, 1), Decimal('100'), Decimal('10'))   # 100 @ 10, total=1000
    t.process('SPLIT', date(2024, 1, 1), Decimal('0.5'), None)             # ratio=0.5 (1:2 reverse)

    assert t.total_quantity() == Decimal('50.000000'), f'qty={t.total_quantity()}'
    assert abs(t.total_cost_basis() - Decimal('1000')) < Decimal('0.01'), f'cost={t.total_cost_basis()}'
    assert t.lots()[0].cost_per_share == Decimal('20.000000'), f'cps={t.lots()[0].cost_per_share}'


def test_drip_included_in_quantity():
    t = LotTracker('VYM')
    t.process('BUY',  date(2023, 1, 1), Decimal('100'), Decimal('50'))
    t.process('DRIP', date(2023, 4, 1), Decimal('2'),   Decimal('51'))   # DRIP: 2 shares @ 51

    assert t.total_quantity() == Decimal('102')


def test_incomplete_history():
    t = LotTracker('MSFT')
    t.process('BUY',  date(2023, 1, 1), Decimal('10'), Decimal('300'))
    t.process('SELL', date(2024, 1, 1), Decimal('50'), None)              # sells more than held

    assert not t.has_complete_history
    assert len(t.warnings) == 1
    assert t.total_quantity() == Decimal('0')


def test_split_with_zero_holdings():
    t = LotTracker('NVDA')
    # No BUY before split — should warn and not crash
    t.process('SPLIT', date(2024, 1, 1), Decimal('4'), None)  # ratio=4 with no holdings

    assert not t.has_complete_history
    assert len(t.warnings) == 1
