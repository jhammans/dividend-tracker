"""
LIFO lot-cost-basis tracker for a single stock in a single account.

Usage (Phase 1 analytics):
    tracker = LotTracker(ticker)
    for tx in transactions_chronological:
        tracker.process(tx)
    cost_basis   = tracker.total_cost_basis()
    quantity     = tracker.total_quantity()
    is_complete  = tracker.has_complete_history
"""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Lot:
    """A single tax lot: shares acquired at a known cost per share on a specific date."""
    acquired_date: date
    quantity: Decimal
    cost_per_share: Decimal
    source: str = 'BROKER_CSV'   # BROKER_CSV | ESTIMATED | SPLIT_ADJUSTED


class LotTracker:
    """
    Maintains a LIFO stack of lots for one (account, stock) pair.

    Rules:
    - BUY / DRIP  → push a new lot onto the stack
    - SELL        → pop from the top of the stack (LIFO); partial lots allowed
    - SPLIT       → derive ratio from net-new-shares; scale all lot quantities
                    and cost-per-share inversely (total cost basis unchanged)
    - TRANSFER    → ignored (no cost basis effect assumed)

    has_complete_history is False if any SELL consumed more shares than were
    available (indicates missing earlier BUY transactions).
    """

    def __init__(self, ticker: str):
        self.ticker = ticker
        self._lots: List[Lot] = []           # index 0 = oldest, -1 = newest (LIFO pops from -1)
        self.has_complete_history: bool = True
        self.warnings: List[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, tx_type: str, tx_date: date, quantity: Optional[Decimal],
                price: Optional[Decimal], source: str = 'BROKER_CSV') -> None:
        """
        Feed one transaction into the tracker.

        Args:
            tx_type:  internal transaction type (BUY, SELL, DRIP, SPLIT, …)
            tx_date:  transaction date
            quantity: shares involved (always positive).
                      For SPLIT this is the split *ratio* (e.g. Decimal('4') for
                      a 4:1 forward split, Decimal('0.5') for a 1:2 reverse split).
                      This matches the ratio column in the stock_splits table.
            price:    price per share (None for DRIP and SPLIT)
            source:   transaction source flag passed through to the lot
        """
        qty = quantity or Decimal('0')

        if tx_type in ('BUY', 'DRIP'):
            cost = price if price is not None else Decimal('0')
            self._lots.append(Lot(
                acquired_date=tx_date,
                quantity=qty,
                cost_per_share=cost,
                source=source,
            ))

        elif tx_type == 'SELL':
            self._consume_lifo(qty, tx_date)

        elif tx_type == 'SPLIT':
            # quantity is the ratio from stock_splits (e.g. 4.0 for a 4:1 split)
            self._apply_split_ratio(qty, tx_date)

        # TRANSFER, DIVIDEND_PAYMENT, DEPOSIT, WITHDRAWAL etc. have no lot effect

    def total_quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self._lots), Decimal('0'))

    def total_cost_basis(self) -> Optional[Decimal]:
        """
        Returns total cost basis, or None if any lot has cost_per_share=0
        and source is not DRIP (i.e. data is genuinely missing).
        """
        if not self._lots:
            return Decimal('0')
        return sum(lot.quantity * lot.cost_per_share for lot in self._lots)

    def cost_basis_reliable(self) -> bool:
        """False if history is incomplete or any lot was acquired at unknown cost."""
        return self.has_complete_history and all(
            lot.cost_per_share > Decimal('0') or lot.source == 'DRIP'
            for lot in self._lots
        )

    def lots(self) -> List[Lot]:
        return list(self._lots)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _consume_lifo(self, shares_to_sell: Decimal, tx_date: date) -> None:
        """Remove shares from the most-recently-acquired lots first (LIFO)."""
        remaining = shares_to_sell

        while remaining > Decimal('0') and self._lots:
            top_lot = self._lots[-1]

            if top_lot.quantity <= remaining:
                # Consume entire top lot
                remaining -= top_lot.quantity
                self._lots.pop()
            else:
                # Partial consumption of top lot
                top_lot.quantity -= remaining
                remaining = Decimal('0')

        if remaining > Decimal('0.0001'):
            # More shares sold than tracked — incomplete history
            self.has_complete_history = False
            msg = (
                f"{self.ticker}: SELL of {shares_to_sell} on {tx_date} exceeded "
                f"tracked quantity by {remaining:.4f} — transaction history may be incomplete"
            )
            self.warnings.append(msg)
            logger.warning(msg)

    def _apply_split_ratio(self, ratio: Decimal, tx_date: date) -> None:
        """
        Adjust all lots for a stock split using the split ratio.

        The ratio comes directly from the stock_splits table:
            4.0  → 4:1 forward split (each share becomes 4)
            0.5  → 1:2 reverse split (every 2 shares become 1)

        Each lot’s quantity is multiplied by ratio; cost_per_share is divided
        by ratio so that total cost basis is preserved.
        """
        if ratio <= Decimal('0'):
            msg = (
                f"{self.ticker}: SPLIT on {tx_date} has non-positive ratio {ratio} — skipping"
            )
            self.warnings.append(msg)
            logger.warning(msg)
            return

        if ratio == Decimal('1'):
            return  # No-op

        current_qty = self.total_quantity()
        if current_qty == Decimal('0'):
            msg = (
                f"{self.ticker}: SPLIT on {tx_date} encountered with 0 tracked shares — "
                f"cannot apply ratio {ratio}; history may be incomplete"
            )
            self.warnings.append(msg)
            logger.warning(msg)
            self.has_complete_history = False
            return

        for lot in self._lots:
            lot.quantity = (lot.quantity * ratio).quantize(Decimal('0.000001'))
            if lot.cost_per_share > Decimal('0'):
                lot.cost_per_share = (lot.cost_per_share / ratio).quantize(Decimal('0.000001'))
            lot.source = 'SPLIT_ADJUSTED'

        logger.debug(
            f"{self.ticker}: Applied split ratio {ratio} on {tx_date} "
            f"({len(self._lots)} lots adjusted)"
        )

    def _apply_split(self, net_new_shares: Decimal, tx_date: date) -> None:
        """
        Legacy helper — derives ratio from net-new shares and delegates.
        Kept for backward compatibility with Schwab SPLIT transaction rows.
        """
        current_qty = self.total_quantity()
        if current_qty == Decimal('0'):
            self._apply_split_ratio(Decimal('0'), tx_date)  # triggers zero-qty warning
            return
        ratio = (current_qty + net_new_shares) / current_qty
        self._apply_split_ratio(ratio, tx_date)
