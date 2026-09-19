"""Vectorized-ish backtester for the same strategy the live engine runs.

Execution model (documented assumptions):
- Signals are computed on a bar's close; entries use that same close.
- Intra-bar stop/target are checked with the bar's high/low, and the stop is
  evaluated BEFORE the target (conservative: assumes the worst hit first).
- Taker fees are charged on entry and exit. Funding is not modeled.
"""
from __future__ import annotations

from .indicators import atr, bollinger, ema, rsi
from .risk import RiskManager
from .strategy import EMACrossoverStrategy, TurtleStrategy, VGASStrategy

BARS_PER_YEAR = {
    "1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040, "30m": 17520,
    "1h": 8760, "2h": 4380, "4h": 2190, "6h": 1460, "8h": 1095,
    "12h": 730, "1d": 365,
}


def fetch_klines_history(client, symbol: str, interval: str, total_bars: int) -> list:
    """Fetch up to `total_bars` klines, paginating backwards in time."""
    rows: list = []
    end_time = None
    while len(rows) < total_bars:
        n = min(1500, total_bars - len(rows))
        batch = client.klines(symbol, interval, limit=n, end_time=end_time)
        if not batch:
            break
        rows = batch + rows
        end_time = batch[0][0] - 1
        if len(batch) < n:
            break
    return rows


def _gross_pnl(pos: dict, exit_price: float) -> float:
    if pos["side"] == "LONG":
        return (exit_price - pos["entry"]) * pos["qty"]
    return (pos["entry"] - exit_price) * pos["qty"]


def _close(pos: dict, exit_price: float, equity: float, fee_rate: float, trades: list, reason: str) -> float:
    exit_fee = fee_rate * pos["qty"] * exit_price
    pnl = _gross_pnl(pos, exit_price) - exit_fee
    equity += pnl
    trades.append({
        "side": pos["side"], "entry": round(pos["entry"], 4), "exit": round(exit_price, 4),
        "qty": pos["qty"], "pnl": round(pnl, 4), "reason": reason,
    })
    return equity


def run_backtest(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    strategy: EMACrossoverStrategy,
    risk: RiskManager,
    step_size,
    min_qty,
    initial_equity: float = 10000.0,
    fee_rate: float = 0.0004,
) -> tuple[list, list[float], float]:
    """Run the strategy over OHLC series.

    Returns (trades, equity_curve, max_drawdown).
    """
    sigs = strategy.series(closes, highs, lows)
    equity = initial_equity
    peak = initial_equity
    max_dd = 0.0
    trades: list = []
    curve: list[float] = []
    pos: dict | None = None

    for i in range(len(closes)):
        action = sigs[i].action
        atr_v = sigs[i].atr

        if pos is not None:
            side = pos["side"]
            hit_exit = None
            if side == "LONG":
                if lows[i] <= pos["sl"]:
                    hit_exit = pos["sl"]
                elif highs[i] >= pos["tp"]:
                    hit_exit = pos["tp"]
            else:
                if highs[i] >= pos["sl"]:
                    hit_exit = pos["sl"]
                elif lows[i] <= pos["tp"]:
                    hit_exit = pos["tp"]

            if hit_exit is not None:
                equity = _close(pos, hit_exit, equity, fee_rate, trades, "sl/tp")
                pos = None
            else:
                opposite = "SHORT" if side == "LONG" else "LONG"
                if action == opposite:
                    equity = _close(pos, closes[i], equity, fee_rate, trades, "reverse")
                    pos = None

        if pos is None and action in ("LONG", "SHORT") and atr_v and atr_v > 0:
            entry = closes[i]
            sl, tp = strategy.levels(entry, atr_v, action)
            qty = risk.position_size(equity, entry, sl, step_size, min_qty)
            if qty > 0:
                equity -= fee_rate * qty * entry  # entry taker fee
                pos = {"side": action, "qty": qty, "entry": entry, "sl": sl, "tp": tp}

        mark = equity if pos is None else equity + _gross_pnl(pos, closes[i])
        curve.append(mark)
        peak = max(peak, mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak)

    if pos is not None:
        equity = _close(pos, closes[-1], equity, fee_rate, trades, "end")
        curve[-1] = equity

    return trades, curve, max_dd


def summarize(trades: list, curve: list[float], initial_equity: float, bars_per_year: int) -> dict:
    final = curve[-1] if curve else initial_equity
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))

    returns = []
    prev = initial_equity
    for e in curve:
        returns.append((e - prev) / prev if prev else 0.0)
        prev = e
    mean = sum(returns) / len(returns) if returns else 0.0
    var = sum((r - mean) ** 2 for r in returns) / len(returns) if returns else 0.0
    std = var ** 0.5
    sharpe = (mean / std) * (bars_per_year ** 0.5) if std > 0 else 0.0

    peak = initial_equity
    max_dd = 0.0
    for e in curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)

    return {
        "initial_equity": initial_equity,
        "final_equity": round(final, 2),
        "total_return_pct": round((final - initial_equity) / initial_equity * 100, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(-gross_loss, 2),
    }


def _turtle_pnl(pos: dict, exit_price: float) -> float:
    total = 0.0
    for entry, qty in pos["units"]:
        total += (exit_price - entry) * qty if pos["side"] == "LONG" else (entry - exit_price) * qty
    return total


def _turtle_close(pos: dict, exit_price: float, equity: float, fee_rate: float, trades: list, reason: str) -> float:
    exit_fee = sum(fee_rate * qty * exit_price for _, qty in pos["units"])
    entry_fee = pos.get("entry_fees", 0.0)
    pnl = _turtle_pnl(pos, exit_price) - entry_fee - exit_fee
    equity += pnl
    total_qty = sum(q for _, q in pos["units"])
    first_entry = pos["units"][0][0]
    trades.append({
        "side": pos["side"], "entry": round(first_entry, 4), "exit": round(exit_price, 4),
        "qty": total_qty, "units": len(pos["units"]), "pnl": round(pnl, 4), "reason": reason,
    })
    return equity


def run_backtest_turtle(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    strategy: TurtleStrategy,
    risk: RiskManager,
    step_size,
    min_qty,
    initial_equity: float = 10000.0,
    fee_rate: float = 0.0004,
) -> tuple[list, list[float], float]:
    """Turtle backtest: Donchian breakout entry, opposite-channel exit,
    2N stop, 0.5N pyramiding. Stop is checked intra-bar before the exit."""
    n = len(closes)
    ep, xp = strategy.entry_period, strategy.exit_period
    atr_s = atr(highs, lows, closes, strategy.atr_period)
    equity = initial_equity
    peak = initial_equity
    max_dd = 0.0
    trades: list = []
    curve: list[float] = []
    pos: dict | None = None

    for i in range(n):
        a = atr_s[i] if i < len(atr_s) else None
        mark = equity
        if i >= ep:
            entry_high = max(highs[i - ep:i])
            entry_low = min(lows[i - ep:i])
            exit_high = max(highs[i - xp:i])
            exit_low = min(lows[i - xp:i])

            if pos is not None:
                side = pos["side"]
                # 1) intrabar stop (worst case first)
                stopped = False
                if side == "LONG" and lows[i] <= pos["stop"]:
                    equity = _turtle_close(pos, pos["stop"], equity, fee_rate, trades, "stop")
                    pos = None
                    stopped = True
                elif side == "SHORT" and highs[i] >= pos["stop"]:
                    equity = _turtle_close(pos, pos["stop"], equity, fee_rate, trades, "stop")
                    pos = None
                    stopped = True
                if not stopped:
                    # 1.5) intrabar take-profit
                    tp_hit = False
                    if pos.get("tp") is not None:
                        if side == "LONG" and highs[i] >= pos["tp"]:
                            equity = _turtle_close(pos, pos["tp"], equity, fee_rate, trades, "tp")
                            pos = None
                            tp_hit = True
                        elif side == "SHORT" and lows[i] <= pos["tp"]:
                            equity = _turtle_close(pos, pos["tp"], equity, fee_rate, trades, "tp")
                            pos = None
                            tp_hit = True
                    if tp_hit:
                        pass
                    # 2) opposite-channel exit
                    elif (side == "LONG" and closes[i] < exit_low) or (side == "SHORT" and closes[i] > exit_high):
                        equity = _turtle_close(pos, closes[i], equity, fee_rate, trades, "exit")
                        pos = None
                    # 3) pyramid
                    elif a and a > 0 and len(pos["units"]) < strategy.max_units:
                        step = a * strategy.add_atr_mult
                        if side == "LONG" and closes[i] >= pos["last_add"] + step:
                            sl = closes[i] - a * strategy.stop_atr_mult
                            qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                            if qty > 0:
                                pos["units"].append((closes[i], qty))
                                pos["entry_fees"] = pos.get("entry_fees", 0.0) + fee_rate * qty * closes[i]
                                pos["last_add"] = closes[i]
                                pos["stop"] = sl
                        elif side == "SHORT" and closes[i] <= pos["last_add"] - step:
                            sl = closes[i] + a * strategy.stop_atr_mult
                            qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                            if qty > 0:
                                pos["units"].append((closes[i], qty))
                                pos["entry_fees"] = pos.get("entry_fees", 0.0) + fee_rate * qty * closes[i]
                                pos["last_add"] = closes[i]
                                pos["stop"] = sl

            if pos is None and a and a > 0:
                if closes[i] > entry_high:
                    sl, tp = strategy.levels(closes[i], a, "LONG")
                    qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                    if qty > 0:
                        pos = {"side": "LONG", "units": [(closes[i], qty)], "last_add": closes[i],
                               "stop": sl, "tp": tp, "entry_fees": fee_rate * qty * closes[i]}
                elif closes[i] < entry_low:
                    sl, tp = strategy.levels(closes[i], a, "SHORT")
                    qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                    if qty > 0:
                        pos = {"side": "SHORT", "units": [(closes[i], qty)], "last_add": closes[i],
                               "stop": sl, "tp": tp, "entry_fees": fee_rate * qty * closes[i]}

        mark = equity if pos is None else equity + _turtle_pnl(pos, closes[i]) - pos.get("entry_fees", 0.0)
        curve.append(mark)
        peak = max(peak, mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak)

    if pos is not None:
        equity = _turtle_close(pos, closes[-1], equity, fee_rate, trades, "end")
        curve[-1] = equity

    return trades, curve, max_dd


def run_backtest_vgas(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    strategy: VGASStrategy,
    risk: RiskManager,
    step_size,
    min_qty,
    initial_equity: float = 10000.0,
    fee_rate: float = 0.0004,
) -> tuple[list, list[float], float]:
    """VGAS backtest: Turtle breakout filtered by ATR volatility, Bollinger
    Bands and RSI. Shares the add/exit/stop mechanics with the Turtle backtest."""
    n = len(closes)
    ep, xp = strategy.entry_period, strategy.exit_period
    atr_s = atr(highs, lows, closes, strategy.atr_period)
    trend_s = ema(closes, strategy.trend_period)
    mid_s, up_s, lo_s = bollinger(closes, strategy.bb_period, strategy.bb_std)
    rsi_s = rsi(closes, strategy.rsi_period)
    equity = initial_equity
    peak = initial_equity
    max_dd = 0.0
    trades: list = []
    curve: list[float] = []
    pos: dict | None = None

    for i in range(n):
        a = atr_s[i] if i < len(atr_s) else None
        if i >= ep:
            entry_high = max(highs[i - ep:i])
            entry_low = min(lows[i - ep:i])
            exit_high = max(highs[i - xp:i])
            exit_low = min(lows[i - xp:i])

            if pos is not None:
                side = pos["side"]
                stopped = False
                if side == "LONG" and lows[i] <= pos["stop"]:
                    equity = _turtle_close(pos, pos["stop"], equity, fee_rate, trades, "stop")
                    pos = None
                    stopped = True
                elif side == "SHORT" and highs[i] >= pos["stop"]:
                    equity = _turtle_close(pos, pos["stop"], equity, fee_rate, trades, "stop")
                    pos = None
                    stopped = True
                if not stopped:
                    # intrabar take-profit
                    tp_hit = False
                    if pos.get("tp") is not None:
                        if side == "LONG" and highs[i] >= pos["tp"]:
                            equity = _turtle_close(pos, pos["tp"], equity, fee_rate, trades, "tp")
                            pos = None
                            tp_hit = True
                        elif side == "SHORT" and lows[i] <= pos["tp"]:
                            equity = _turtle_close(pos, pos["tp"], equity, fee_rate, trades, "tp")
                            pos = None
                            tp_hit = True
                    if not tp_hit:
                        if (side == "LONG" and closes[i] < exit_low) or (side == "SHORT" and closes[i] > exit_high):
                            equity = _turtle_close(pos, closes[i], equity, fee_rate, trades, "exit")
                            pos = None
                        elif a and a > 0 and len(pos["units"]) < strategy.max_units:
                            step = a * strategy.add_atr_mult
                            if side == "LONG" and closes[i] >= pos["last_add"] + step:
                                sl = closes[i] - a * strategy.stop_atr_mult
                                qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                                if qty > 0:
                                    pos["units"].append((closes[i], qty))
                                    pos["entry_fees"] = pos.get("entry_fees", 0.0) + fee_rate * qty * closes[i]
                                    pos["last_add"] = closes[i]
                                    pos["stop"] = sl
                            elif side == "SHORT" and closes[i] <= pos["last_add"] - step:
                                sl = closes[i] + a * strategy.stop_atr_mult
                                qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                                if qty > 0:
                                    pos["units"].append((closes[i], qty))
                                    pos["entry_fees"] = pos.get("entry_fees", 0.0) + fee_rate * qty * closes[i]
                                    pos["last_add"] = closes[i]
                                    pos["stop"] = sl

            if pos is None and a and a > 0:
                r = rsi_s[i] if i < len(rsi_s) else None
                mid = mid_s[i] if i < len(mid_s) else None
                up = up_s[i] if i < len(up_s) else None
                lo = lo_s[i] if i < len(lo_s) else None
                trend = trend_s[i] if i < len(trend_s) else None
                vol_ok = (a / closes[i]) >= strategy.min_atr_pct
                if vol_ok and r is not None and mid is not None and trend is not None:
                    if (closes[i] > entry_high and closes[i] > trend and mid <= closes[i] <= up
                            and strategy.rsi_long_min <= r <= strategy.rsi_long_max):
                        sl, tp = strategy.levels(closes[i], a, "LONG")
                        qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                        if qty > 0:
                            pos = {"side": "LONG", "units": [(closes[i], qty)], "last_add": closes[i],
                                   "stop": sl, "tp": tp, "entry_fees": fee_rate * qty * closes[i]}
                    elif (closes[i] < entry_low and closes[i] < trend and lo <= closes[i] <= mid
                            and strategy.rsi_short_min <= r <= strategy.rsi_short_max):
                        sl, tp = strategy.levels(closes[i], a, "SHORT")
                        qty = risk.position_size(equity, closes[i], sl, step_size, min_qty)
                        if qty > 0:
                            pos = {"side": "SHORT", "units": [(closes[i], qty)], "last_add": closes[i],
                                   "stop": sl, "tp": tp, "entry_fees": fee_rate * qty * closes[i]}

        mark = equity if pos is None else equity + _turtle_pnl(pos, closes[i]) - pos.get("entry_fees", 0.0)
        curve.append(mark)
        peak = max(peak, mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak)

    if pos is not None:
        equity = _turtle_close(pos, closes[-1], equity, fee_rate, trades, "end")
        curve[-1] = equity

    return trades, curve, max_dd
