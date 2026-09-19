"""Backtest CLI: run the EMA or Turtle strategy over historical klines.

Usage:
    python backtest.py --strategy turtle --interval 15m --bars 4000
    python backtest.py --strategy turtle --symbol ETHUSDT --interval 1h
    python backtest.py --strategy ema --interval 1h --ema-slow 55
"""
from __future__ import annotations

import argparse

import config
from binance_futures import BinanceFuturesClient
from quant import EMACrossoverStrategy, RiskManager, TurtleStrategy, VGASStrategy
from quant.backtest import (
    BARS_PER_YEAR,
    fetch_klines_history,
    run_backtest,
    run_backtest_turtle,
    run_backtest_vgas,
    summarize,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the futures strategy")
    parser.add_argument("--strategy", choices=["vgas", "turtle", "ema"], default="vgas")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--bars", type=int, default=4000, help="number of historical bars")
    parser.add_argument("--equity", type=float, default=10000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.015)
    parser.add_argument("--max-leverage", type=int, default=5)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    # EMA params
    parser.add_argument("--ema-fast", type=int, default=9)
    parser.add_argument("--ema-slow", type=int, default=55)
    parser.add_argument("--trend-filter", type=int, default=100)
    parser.add_argument("--atr-mult", type=float, default=2.0)
    parser.add_argument("--risk-reward", type=float, default=2.0)
    # Turtle params
    parser.add_argument("--entry-period", type=int, default=20)
    parser.add_argument("--exit-period", type=int, default=10)
    parser.add_argument("--atr-period", type=int, default=20)
    parser.add_argument("--stop-atr-mult", type=float, default=2.0)
    parser.add_argument("--add-atr-mult", type=float, default=0.5)
    parser.add_argument("--max-units", type=int, default=3)
    # VGAS filters
    parser.add_argument("--trend-period", type=int, default=55)
    parser.add_argument("--bb-period", type=int, default=20)
    parser.add_argument("--bb-std", type=float, default=2.0)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--rsi-long-min", type=float, default=50.0)
    parser.add_argument("--rsi-long-max", type=float, default=80.0)
    parser.add_argument("--rsi-short-max", type=float, default=50.0)
    parser.add_argument("--rsi-short-min", type=float, default=20.0)
    parser.add_argument("--min-atr-pct", type=float, default=0.002)
    args = parser.parse_args()

    client = BinanceFuturesClient(config.API_KEY, config.API_SECRET, config.REST_BASE_URL)
    filters = client.symbol_filters(args.symbol)

    print(f"Fetching {args.bars} bars of {args.symbol} {args.interval} ...")
    rows = fetch_klines_history(client, args.symbol, args.interval, args.bars)
    warmup = max(args.entry_period, args.ema_slow) + 1
    if len(rows) < warmup:
        print(f"Not enough data ({len(rows)} bars).")
        return

    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    print(f"Backtesting {len(closes)} bars | {closes[0]:.2f} -> {closes[-1]:.2f} "
          f"(market move {(closes[-1] / closes[0] - 1) * 100:+.2f}%)")

    risk = RiskManager(args.risk_per_trade, args.max_leverage, 0.5)
    if args.strategy == "vgas":
        strategy = VGASStrategy(
            args.entry_period, args.exit_period, args.trend_period, args.atr_period,
            args.bb_period, args.bb_std, args.rsi_period,
            args.rsi_long_min, args.rsi_long_max, args.rsi_short_max, args.rsi_short_min,
            args.min_atr_pct, args.stop_atr_mult, args.add_atr_mult, args.max_units, args.risk_reward,
        )
        trades, curve, _ = run_backtest_vgas(
            closes, highs, lows, strategy, risk,
            step_size=filters.step_size, min_qty=filters.min_qty,
            initial_equity=args.equity, fee_rate=args.fee_rate,
        )
    elif args.strategy == "turtle":
        strategy = TurtleStrategy(
            args.entry_period, args.exit_period, args.atr_period,
            args.stop_atr_mult, args.add_atr_mult, args.max_units, args.risk_reward,
        )
        trades, curve, _ = run_backtest_turtle(
            closes, highs, lows, strategy, risk,
            step_size=filters.step_size, min_qty=filters.min_qty,
            initial_equity=args.equity, fee_rate=args.fee_rate,
        )
    else:
        strategy = EMACrossoverStrategy(
            args.ema_fast, args.ema_slow, args.trend_filter,
            args.atr_period, args.atr_mult, args.risk_reward,
        )
        trades, curve, _ = run_backtest(
            closes, highs, lows, strategy, risk,
            step_size=filters.step_size, min_qty=filters.min_qty,
            initial_equity=args.equity, fee_rate=args.fee_rate,
        )

    m = summarize(trades, curve, args.equity, BARS_PER_YEAR.get(args.interval, 35040))
    print("\n================ BACKTEST RESULTS ================")
    print(f"Strategy          : {args.strategy.upper()}")
    print(f"Symbol/interval   : {args.symbol} {args.interval}")
    if args.strategy == "vgas":
        print(f"VGAS              : entry={args.entry_period} exit={args.exit_period} trend={args.trend_period} "
              f"ATR={args.atr_period} stop={args.stop_atr_mult}N units={args.max_units}")
        print(f"Filters           : BB({args.bb_period},{args.bb_std}) RSI({args.rsi_period}) "
              f"long[{args.rsi_long_min},{args.rsi_long_max}] short[{args.rsi_short_min},{args.rsi_short_max}] "
              f"minATR={args.min_atr_pct*100:.1f}%")
    elif args.strategy == "turtle":
        print(f"Turtle            : entry={args.entry_period} exit={args.exit_period} ATR={args.atr_period} "
              f"stop={args.stop_atr_mult}N add={args.add_atr_mult}N units={args.max_units}")
    else:
        print(f"EMA               : {args.ema_fast}/{args.ema_slow} trend={args.trend_filter} "
              f"ATR={args.atr_period} mult={args.atr_mult} rr={args.risk_reward}")
    print(f"Risk/unit         : {args.risk_per_trade*100:.1f}%")
    print(f"Initial equity    : {m['initial_equity']:.2f} USDT")
    print(f"Final equity      : {m['final_equity']:.2f} USDT")
    print(f"Total return      : {m['total_return_pct']:+.2f}%")
    print(f"Trades            : {m['trades']}")
    print(f"Win rate          : {m['win_rate_pct']:.1f}%")
    print(f"Profit factor     : {m['profit_factor']}")
    print(f"Max drawdown      : {m['max_drawdown_pct']:.2f}%")
    print(f"Sharpe (annual)   : {m['sharpe']}")
    print(f"Gross profit      : {m['gross_profit']:.2f} USDT")
    print(f"Gross loss        : {m['gross_loss']:.2f} USDT")
    print("==================================================")

    if trades:
        print("\nLast 10 trades:")
        for t in trades[-10:]:
            u = f" x{t.get('units', 1)}" if t.get("units", 1) > 1 else ""
            print(f"  {t['side']:<5} entry={t['entry']:<12} exit={t['exit']:<12} "
                  f"qty={t['qty']:<10}{u} pnl={t['pnl']:+.2f}  {t['reason']}")


if __name__ == "__main__":
    main()
