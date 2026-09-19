"""Risk management: position sizing and circuit breakers."""
from __future__ import annotations

from decimal import Decimal


class RiskManager:
    def __init__(self, risk_per_trade: float, max_leverage: int, max_position_pct: float):
        self.risk_per_trade = risk_per_trade
        self.max_leverage = max_leverage
        self.max_position_pct = max_position_pct

    def position_size(
        self,
        equity: float,
        entry: float,
        stop: float,
        step_size,
        min_qty,
    ) -> float:
        """Size a position so a stop-out loses exactly `risk_per_trade` of
        equity (USDT-margined: loss = |entry - stop| * qty). Then cap the
        notional and round down to the symbol's step size.

        Returns 0.0 when the computed size is below the minimum lot.
        """
        equity_d = Decimal(str(equity))
        entry_d = Decimal(str(entry))
        stop_d = Decimal(str(stop))
        step_d = Decimal(str(step_size))
        min_qty_d = Decimal(str(min_qty))

        distance = abs(entry_d - stop_d)
        if distance <= 0 or equity_d <= 0:
            return 0.0

        risk_amount = equity_d * Decimal(str(self.risk_per_trade))
        qty = risk_amount / distance

        max_notional = equity_d * Decimal(str(self.max_position_pct)) * Decimal(self.max_leverage)
        if entry_d > 0:
            qty = min(qty, max_notional / entry_d)

        if step_d > 0:
            qty = (qty // step_d) * step_d
        if qty < min_qty_d:
            return 0.0
        return float(qty)


class CircuitBreaker:
    """Tracks equity and halts trading on drawdown / daily loss limits."""

    def __init__(self, max_drawdown_pct: float, daily_loss_limit_pct: float):
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.peak_equity: float | None = None
        self.daily_date: str | None = None
        self.daily_start_equity: float | None = None

    def check(self, equity: float, today: str) -> str | None:
        """Return a halt reason string, or None if within limits."""
        if self.peak_equity is None:
            self.peak_equity = equity
        self.peak_equity = max(self.peak_equity, equity)

        if self.daily_date != today or self.daily_start_equity is None:
            self.daily_date = today
            self.daily_start_equity = equity

        drawdown = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        if drawdown >= self.max_drawdown_pct:
            return f"max drawdown {drawdown:.2%} >= limit {self.max_drawdown_pct:.2%}"

        daily_pnl = equity - self.daily_start_equity
        limit = self.daily_loss_limit_pct * self.daily_start_equity
        if daily_pnl <= -limit:
            return f"daily loss {daily_pnl:.2f} <= -{limit:.2f}"

        return None
