"""Systematic parameter sweep with a train/test split to expose overfitting.

For each interval it fetches history, splits it chronologically:
- TRAIN (first `split` fraction) — used to rank parameter sets.
- TEST  (last  `1-split` fraction) — held out, never used for ranking.

Overfitting shows up as: a parameter set that looks great in-sample but does
poorly out-of-sample. The tool reports BOTH, plus the gap between them.

Usage:
    python optimize.py                                   # BTCUSDT, 15m/1h/4h
    python optimize.py --intervals 1h,4h --bars 5000
    python optimize.py --ema-fast 5,9,13 --trend-filter 0,100,200
"""
from __future__ import annotations

import argparse
import itertools

import config
from binance_futures import BinanceFuturesClient
from quant import EMACrossoverStrategy, RiskManager
from quant.backtest import BARS_PER_YEAR, fetch_klines_history, run_backtest, summarize


def parse_ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def parse_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def build_grid(args) -> dict:
    return {
        "ema_fast": parse_ints(args.ema_fast),
        "ema_slow": parse_ints(args.ema_slow),
        "trend_filter_period": parse_ints(args.trend_filter),
        "atr_period": parse_ints(args.atr_period),
        "atr_multiplier": parse_floats(args.atr_mult),
        "risk_reward": parse_floats(args.risk_reward),
    }


def fmt_params(p: dict) -> str:
    return (f"ema {p['ema_fast']}/{p['ema_slow']}  tf={p['trend_filter_period']}  "
            f"atrP={p['atr_period']} atrM={p['atr_multiplier']}  rr={p['risk_reward']}")


def sweep_interval(client, symbol, interval, grid, args, bars_per_year) -> list[dict]:
    rows = fetch_klines_history(client, symbol, interval, args.bars)
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]

    filters = client.symbol_filters(symbol)
    step = filters.step_size
    minq = filters.min_qty
    split_idx = int(len(closes) * args.split)

    results: list[dict] = []
    keys, value_lists = zip(*grid.items())
    total = 1
    for v in value_lists:
        total *= len(v)

    for idx, combo in enumerate(itertools.product(*value_lists), 1):
        params = dict(zip(keys, combo))
        strat = EMACrossoverStrategy(
            params["ema_fast"], params["ema_slow"], params["trend_filter_period"],
            params["atr_period"], params["atr_multiplier"], params["risk_reward"],
        )
        risk = RiskManager(args.risk_per_trade, args.max_leverage, 0.5)

        trades_t, curve_t, _ = run_backtest(
            closes[:split_idx], highs[:split_idx], lows[:split_idx],
            strat, risk, step, minq, args.equity, args.fee_rate,
        )
        trades_v, curve_v, _ = run_backtest(
            closes[split_idx:], highs[split_idx:], lows[split_idx:],
            strat, risk, step, minq, args.equity, args.fee_rate,
        )
        m_t = summarize(trades_t, curve_t, args.equity, bars_per_year)
        m_v = summarize(trades_v, curve_v, args.equity, bars_per_year)
        results.append({
            "params": params,
            "train_ret": m_t["total_return_pct"],
            "test_ret": m_v["total_return_pct"],
            "train_pf": m_t["profit_factor"],
            "test_pf": m_v["profit_factor"],
            "train_trades": m_t["trades"],
            "test_trades": m_v["trades"],
            "train_dd": m_t["max_drawdown_pct"],
            "test_dd": m_v["max_drawdown_pct"],
            "train_sharpe": m_t["sharpe"],
            "test_sharpe": m_v["sharpe"],
        })
        if idx % 50 == 0 or idx == total:
            print(f"  ... {idx}/{total} combos evaluated", flush=True)

    return results, closes


def report(interval: str, results: list[dict], closes: list[float]) -> None:
    n = len(results)
    by_train = sorted(results, key=lambda r: r["train_ret"], reverse=True)
    by_test = sorted(results, key=lambda r: r["test_ret"], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"INTERVAL {interval}   ({len(closes)} bars | market move "
          f"{(closes[-1] / closes[0] - 1) * 100:+.2f}%)   {n} parameter sets")
    print(f"{'=' * 100}")

    print(f"\nTop 8 by IN-SAMPLE (train) return:")
    print(f"  {'train%':>8} {'test%':>8} {'gap%':>8} {'PF(train)':>9} {'dd%':>6} {'trades':>6}  params")
    for r in by_train[:8]:
        gap = r["train_ret"] - r["test_ret"]
        print(f"  {r['train_ret']:>8.2f} {r['test_ret']:>8.2f} {gap:>8.2f} "
              f"{r['train_pf']:>9.2f} {r['train_dd']:>6.1f} {r['train_trades']:>6}  {fmt_params(r['params'])}")

    print(f"\nTop 8 by OUT-OF-SAMPLE (test) return:")
    print(f"  {'test%':>8} {'train%':>8} {'PF(test)':>9} {'dd%':>6} {'trades':>6}  params")
    for r in by_test[:8]:
        print(f"  {r['test_ret']:>8.2f} {r['train_ret']:>8.2f} "
              f"{r['test_pf']:>9.2f} {r['test_dd']:>6.1f} {r['test_trades']:>6}  {fmt_params(r['params'])}")

    train_rets = sorted(r["train_ret"] for r in results)
    test_rets = sorted(r["test_ret"] for r in results)
    n_prof_test = sum(1 for r in results if r["test_ret"] > 0)
    n_prof_both = sum(1 for r in results if r["train_ret"] > 0 and r["test_ret"] > 0)
    med = n // 2
    print(f"\nDistribution:")
    print(f"  train return: min={train_rets[0]:.1f}%  median={train_rets[med]:.1f}%  max={train_rets[-1]:.1f}%")
    print(f"  test  return: min={test_rets[0]:.1f}%  median={test_rets[med]:.1f}%  max={test_rets[-1]:.1f}%")
    print(f"  profitable out-of-sample: {n_prof_test}/{n} ({n_prof_test/n*100:.0f}%)")
    print(f"  profitable in BOTH train & test: {n_prof_both}/{n} ({n_prof_both/n*100:.0f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parameter sweep with train/test overfitting check")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--bars", type=int, default=4000)
    parser.add_argument("--split", type=float, default=0.7)
    parser.add_argument("--equity", type=float, default=10000.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--max-leverage", type=int, default=5)
    parser.add_argument("--ema-fast", default="5,9,13")
    parser.add_argument("--ema-slow", default="21,34,55")
    parser.add_argument("--trend-filter", default="0,100,200")
    parser.add_argument("--atr-period", default="14")
    parser.add_argument("--atr-mult", default="2.0,3.0")
    parser.add_argument("--risk-reward", default="2.0,3.0")
    args = parser.parse_args()

    grid = build_grid(args)
    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)
    intervals = [i for i in args.intervals.split(",") if i.strip()]

    client = BinanceFuturesClient(config.API_KEY, config.API_SECRET, config.REST_BASE_URL)
    print(f"Symbol={args.symbol}  intervals={intervals}")
    print(f"Grid: {grid}")
    print(f"Total combos per interval: {total_combos}\n")

    for interval in intervals:
        print(f"Fetching {args.bars} bars of {args.symbol} {interval} ...", flush=True)
        results, closes = sweep_interval(client, args.symbol, interval, grid, args,
                                         BARS_PER_YEAR.get(interval, 35040))
        report(interval, results, closes)

    print("\nNote: test set is held out during ranking, but once you select from it, "
          "it is no longer pristine. A third untouched window is needed for a final verdict.")


if __name__ == "__main__":
    main()
