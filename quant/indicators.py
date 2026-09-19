"""Technical indicators (pure Python, no numpy dependency)."""
from __future__ import annotations


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average, seeded with the SMA of the first `period`
    values. Returns a list of the same length as `values`."""
    n = len(values)
    if n == 0:
        return []
    out = [0.0] * n
    if period <= 0:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period if n >= period else sum(values) / n
    start = min(period - 1, n - 1)
    for i in range(start + 1):
        out[i] = seed
    for i in range(start + 1, n):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float | None]:
    """Average True Range (Wilder smoothing). Warmup bars are `None`."""
    n = len(closes)
    tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    out: list[float | None] = [None] * n
    if n == 0 or period <= 0:
        return out
    if n < period:
        return out
    seed = sum(tr[:period]) / period
    out[period - 1] = seed
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period  # type: ignore[operator]
    return out


def bollinger(closes: list[float], period: int, num_std: float = 2.0):
    """Bollinger Bands. Returns (mid, upper, lower) lists; warmup bars are None."""
    n = len(closes)
    mids: list[float | None] = [None] * n
    uppers: list[float | None] = [None] * n
    lowers: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        mids[i] = m
        uppers[i] = m + num_std * sd
        lowers[i] = m - num_std * sd
    return mids, uppers, lowers


def rsi(closes: list[float], period: int) -> list[float | None]:
    """Relative Strength Index (Wilder smoothing). Warmup bars are `None`."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains[i] = max(diff, 0.0)
        losses[i] = max(-diff, 0.0)
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out
