"""Precision / formatting helpers for order quantities and prices.

Binance rejects orders whose quantity/price don't align with the symbol's
LOT_SIZE (stepSize) and PRICE_FILTER (tickSize). These helpers round values
*down* to a valid step and format them with the right number of decimals.
"""
from __future__ import annotations

from decimal import Decimal


def format_step_value(value, step) -> str:
    """Round `value` down to the nearest multiple of `step`, formatted with
    exactly the number of decimals that `step` implies.

    Examples:
        format_step_value("1.239", "0.01")  -> "1.23"
        format_step_value("1.239", "0.001") -> "1.239"
        format_step_value("0.5",   "1")     -> "0"
    """
    step = Decimal(str(step)).normalize()
    value = Decimal(str(value))
    if step <= 0:
        return str(value)
    rounded = (value // step) * step
    decimals = max(0, -step.as_tuple().exponent)
    return f"{rounded:.{decimals}f}"


class SymbolFilters:
    """Convenience wrapper around one symbol's exchangeInfo filters."""

    def __init__(self, symbol_info: dict):
        self.symbol = symbol_info["symbol"]
        self._filters = {f["filterType"]: f for f in symbol_info.get("filters", [])}

    def _filter(self, name: str) -> dict | None:
        return self._filters.get(name)

    @property
    def tick_size(self) -> Decimal:
        return Decimal(self._filter("PRICE_FILTER")["tickSize"])

    @property
    def step_size(self) -> Decimal:
        return Decimal(self._filter("LOT_SIZE")["stepSize"])

    @property
    def min_qty(self) -> Decimal:
        return Decimal(self._filter("LOT_SIZE")["minQty"])

    @property
    def min_notional(self) -> Decimal:
        nf = self._filter("MIN_NOTIONAL")
        return Decimal(nf["notional"]) if nf else Decimal(0)

    def format_price(self, price) -> str:
        return format_step_value(price, self.tick_size)

    def format_qty(self, qty) -> str:
        return format_step_value(qty, self.step_size)
