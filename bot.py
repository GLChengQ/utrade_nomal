"""Live trading bot entry point.

Runs the configured strategy (Turtle by default) with strict risk management
on the Binance USDS-M Futures testnet. On entry it places exchange-native
protective stop-loss (and take-profit when the strategy defines one).

Usage:
    python bot.py               # run continuously (poll every BOT_POLL_SECONDS)
    python bot.py --once        # run one cycle and exit (useful for testing)
    python bot.py --dry-run     # simulate decisions without placing orders
    python bot.py --symbol ETHUSDT --interval 15m
"""
from __future__ import annotations

import argparse
import logging

import config
from binance_futures import BinanceFuturesClient
from quant import EMACrossoverStrategy, RiskManager, TradingEngine, TurtleStrategy, VGASStrategy


def build_strategy(cfg):
    if cfg.strategy_type == "ema":
        return EMACrossoverStrategy(
            cfg.ema_fast, cfg.ema_slow, cfg.trend_filter_period,
            cfg.atr_period, cfg.atr_multiplier, cfg.risk_reward,
        )
    if cfg.strategy_type == "turtle":
        return TurtleStrategy(
            cfg.entry_period, cfg.exit_period, cfg.atr_period,
            cfg.stop_atr_mult, cfg.add_atr_mult, cfg.max_units, cfg.risk_reward,
        )
    return VGASStrategy(
        cfg.entry_period, cfg.exit_period, cfg.trend_period, cfg.atr_period,
        cfg.bb_period, cfg.bb_std, cfg.rsi_period,
        cfg.rsi_long_min, cfg.rsi_long_max, cfg.rsi_short_max, cfg.rsi_short_min,
        cfg.min_atr_pct, cfg.stop_atr_mult, cfg.add_atr_mult, cfg.max_units, cfg.risk_reward,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance USDS-M Futures quant bot")
    parser.add_argument("--once", action="store_true", help="run a single cycle then exit")
    parser.add_argument("--dry-run", action="store_true", help="simulate, do not place orders")
    parser.add_argument("--symbol", help="override trading symbol")
    parser.add_argument("--interval", help="override kline interval")
    parser.add_argument("--no-session", action="store_true", help="ignore trading hours (trade any time)")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    cfg = config.BotConfig.from_env()
    if args.symbol:
        cfg.symbols = [args.symbol]
    if args.interval:
        cfg.interval = args.interval
    if args.dry_run:
        cfg.dry_run = True
    if args.no_session:
        cfg.session_enabled = False

    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        ],
    )

    client = BinanceFuturesClient(
        config.API_KEY,
        config.API_SECRET,
        config.REST_BASE_URL,
        recv_window=config.RECV_WINDOW,
        timeout=config.REQUEST_TIMEOUT,
    )

    # Auto-expand the watchlist to the curated liquid universe (first N).
    if cfg.symbols_auto and not args.symbol:
        cfg.symbols = list(config.DEFAULT_SYMBOLS[:cfg.max_symbols])
    logging.getLogger("bot").info("watchlist (%d): %s ...", len(cfg.symbols), ",".join(cfg.symbols[:8]))

    strategy = build_strategy(cfg)
    risk = RiskManager(cfg.risk_per_trade, cfg.max_leverage, cfg.max_position_pct)
    engine = TradingEngine(client, strategy, risk, cfg, config.STATE_PATH, dry_run=cfg.dry_run)

    engine.run(once=args.once)


if __name__ == "__main__":
    main()
