"""Trend-following strategy: EMA crossover with ATR stop/target and an
optional longer-EMA regime filter.

The regime filter only allows LONG entries when price is above a longer EMA
and SHORT entries when price is below it. This is a standard technique to
avoid counter-trend whipsaw trades in range/choppy markets.
"""
from __future__ import annotations

from dataclasses import dataclass

from .indicators import atr, bollinger, ema, rsi


@dataclass
class Signal:
    action: str  # "LONG" | "SHORT" | "FLAT"
    reason: str = ""
    atr: float | None = None


@dataclass
class EMACrossoverStrategy:
    ema_fast: int = 9
    ema_slow: int = 21
    trend_filter_period: int = 100          # 0 = off
    atr_period: int = 14
    atr_multiplier: float = 2.0
    risk_reward: float = 2.0

    @property
    def warmup(self) -> int:
        return max(self.ema_slow, self.trend_filter_period)

    def _crosses(self, prev_fast, prev_slow, cur_fast, cur_slow) -> str:
        if prev_fast <= prev_slow and cur_fast > cur_slow:
            return "LONG"
        if prev_fast >= prev_slow and cur_fast < cur_slow:
            return "SHORT"
        return "FLAT"

    def series(self, closes: list[float], highs: list[float], lows: list[float]) -> list[Signal]:
        """Per-bar signal list (aligned with the input arrays)."""
        n = len(closes)
        sigs: list[Signal] = [Signal("FLAT", "insufficient data")] * n
        if n < self.warmup + 1:
            return sigs

        fast = ema(closes, self.ema_fast)
        slow = ema(closes, self.ema_slow)
        trend = ema(closes, self.trend_filter_period) if self.trend_filter_period > 0 else None
        atrs = atr(highs, lows, closes, self.atr_period)

        for i in range(self.warmup, n):
            action = self._crosses(fast[i - 1], slow[i - 1], fast[i], slow[i])
            if action == "LONG" and trend is not None and closes[i] < trend[i]:
                action = "FLAT"
            elif action == "SHORT" and trend is not None and closes[i] > trend[i]:
                action = "FLAT"

            if action == "LONG":
                sigs[i] = Signal("LONG", f"golden cross EMA{self.ema_fast}/{self.ema_slow}", atrs[i])
            elif action == "SHORT":
                sigs[i] = Signal("SHORT", f"death cross EMA{self.ema_fast}/{self.ema_slow}", atrs[i])
            else:
                sigs[i] = Signal("FLAT", "no cross / filtered", atrs[i])
        return sigs

    def evaluate(self, closes: list[float], highs: list[float], lows: list[float],
                 position_side: str | None = None) -> Signal:
        """Latest signal only (used by the live engine)."""
        sigs = self.series(closes, highs, lows)
        return sigs[-1] if sigs else Signal("FLAT", "no data")

    def levels(self, entry: float, atr_value: float, side: str) -> tuple[float, float]:
        """Return (stop_loss, take_profit) around `entry` for the given side."""
        distance = atr_value * self.atr_multiplier
        if side == "LONG":
            return entry - distance, entry + distance * self.risk_reward
        return entry + distance, entry - distance * self.risk_reward


@dataclass
class TurtleStrategy:
    """Turtle-style Donchian breakout with ATR stop and pyramiding.

    - Entry: close breaks the N-bar high (long) or N-bar low (short).
    - Exit:  close breaks the opposite M-bar channel.
    - Stop:  2 * ATR from the most recent entry (moved up on each add).
    - Add:   pyramid one unit every 0.5 * ATR in favour, up to `max_units`.

    Fixed take-profit = risk_reward * stop distance; exit breakout is the fallback.
    """
    entry_period: int = 20
    exit_period: int = 10
    atr_period: int = 20
    stop_atr_mult: float = 2.0
    add_atr_mult: float = 0.5
    max_units: int = 3
    risk_reward: float = 2.0

    @property
    def warmup(self) -> int:
        return self.entry_period + 1

    def evaluate(self, closes: list[float], highs: list[float], lows: list[float],
                 position_side: str | None = None) -> Signal:
        n = len(closes)
        if n < self.entry_period + 1:
            return Signal("FLAT", "insufficient data")
        a = atr(highs, lows, closes, self.atr_period)[-1]
        cur = closes[-1]
        # Channel over the PRIOR N bars (exclude the current, forming bar).
        entry_high = max(highs[-(self.entry_period + 1):-1])
        entry_low = min(lows[-(self.entry_period + 1):-1])
        exit_high = max(highs[-(self.exit_period + 1):-1])
        exit_low = min(lows[-(self.exit_period + 1):-1])

        if position_side == "LONG" and cur < exit_low:
            return Signal("EXIT", f"close below {self.exit_period}-bar low", a)
        if position_side == "SHORT" and cur > exit_high:
            return Signal("EXIT", f"close above {self.exit_period}-bar high", a)
        if position_side is None:
            if cur > entry_high:
                return Signal("LONG", f"breakout above {self.entry_period}-bar high", a)
            if cur < entry_low:
                return Signal("SHORT", f"breakout below {self.entry_period}-bar low", a)
        return Signal("FLAT", "no breakout", a)

    def levels(self, entry: float, atr_value: float, side: str) -> tuple[float, float | None]:
        """Stop at 2*ATR; take-profit at risk_reward * stop distance."""
        distance = atr_value * self.stop_atr_mult
        if side == "LONG":
            return entry - distance, entry + distance * self.risk_reward
        return entry + distance, entry - distance * self.risk_reward


@dataclass
class VGASStrategy:
    """Volatility-adaptive trend following: dual Donchian channels (VGAS)
    combined with Bollinger Bands and RSI to filter out false breakouts.

    LONG entry requires ALL of:
      1. close breaks above the fast N-bar high (breakout trigger)
      2. close above the slow trend EMA (uptrend regime)
      3. ATR/close >= min_atr_pct (enough volatility, not dead/choppy)
      4. close >= Bollinger middle band (bullish) and <= upper band (not overextended)
      5. RSI in [rsi_long_min, rsi_long_max] (bullish momentum, not overbought)
    SHORT is the mirror. Exit = opposite fast channel breakout or 2*ATR stop.
    """
    entry_period: int = 20
    exit_period: int = 10
    trend_period: int = 55
    atr_period: int = 20
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_long_min: float = 50.0
    rsi_long_max: float = 80.0
    rsi_short_max: float = 50.0
    rsi_short_min: float = 20.0
    min_atr_pct: float = 0.002          # 0.2% minimum volatility
    stop_atr_mult: float = 2.0
    add_atr_mult: float = 0.5
    max_units: int = 3
    risk_reward: float = 2.0

    @property
    def warmup(self) -> int:
        return max(self.entry_period, self.trend_period, self.bb_period, self.rsi_period) + 1

    def evaluate(self, closes: list[float], highs: list[float], lows: list[float],
                 position_side: str | None = None) -> Signal:
        n = len(closes)
        if n < self.warmup:
            return Signal("FLAT", "insufficient data")

        cur = closes[-1]
        a = atr(highs, lows, closes, self.atr_period)[-1]
        trend = ema(closes, self.trend_period)[-1]
        mids, uppers, lowers = bollinger(closes, self.bb_period, self.bb_std)
        mid, upper, lower = mids[-1], uppers[-1], lowers[-1]
        r = rsi(closes, self.rsi_period)[-1]

        # Channels over the PRIOR bars (exclude the current forming bar).
        entry_high = max(highs[-(self.entry_period + 1):-1])
        entry_low = min(lows[-(self.entry_period + 1):-1])
        exit_high = max(highs[-(self.exit_period + 1):-1])
        exit_low = min(lows[-(self.exit_period + 1):-1])

        # Exit (in position).
        if position_side == "LONG" and cur < exit_low:
            return Signal("EXIT", f"close below {self.exit_period}-bar low", a)
        if position_side == "SHORT" and cur > exit_high:
            return Signal("EXIT", f"close above {self.exit_period}-bar high", a)

        if position_side is not None or a is None or a <= 0 or mid is None or r is None:
            return Signal("FLAT", "no signal / filtered", a)

        vol_ok = (a / cur) >= self.min_atr_pct
        if not vol_ok:
            return Signal("FLAT", "volatility filter", a)

        # Diagnostic breakdown for the flat case (why no entry?).
        if cur > entry_high:  # upside breakout happened
            if not (cur > trend):
                return Signal("FLAT", "trend filter", a)
            if not (mid <= cur <= upper):
                return Signal("FLAT", "bollinger filter", a)
            if not (self.rsi_long_min <= r <= self.rsi_long_max):
                return Signal("FLAT", "rsi filter", a)
            return Signal("LONG", "VGAS long breakout", a)
        if cur < entry_low:  # downside breakout happened
            if not (cur < trend):
                return Signal("FLAT", "trend filter", a)
            if not (lower <= cur <= mid):
                return Signal("FLAT", "bollinger filter", a)
            if not (self.rsi_short_min <= r <= self.rsi_short_max):
                return Signal("FLAT", "rsi filter", a)
            return Signal("SHORT", "VGAS short breakout", a)
        return Signal("FLAT", "no breakout", a)

    def levels(self, entry: float, atr_value: float, side: str) -> tuple[float, float | None]:
        distance = atr_value * self.stop_atr_mult
        if side == "LONG":
            return entry - distance, entry + distance * self.risk_reward
        return entry + distance, entry - distance * self.risk_reward
