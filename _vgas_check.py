import config
from binance_futures import BinanceFuturesClient
from quant import VGASStrategy

cfg = config.BotConfig.from_env()
client = BinanceFuturesClient(config.API_KEY, config.API_SECRET, config.REST_BASE_URL)
symbols = list(config.DEFAULT_SYMBOLS[:cfg.max_symbols])
strat = VGASStrategy(
    cfg.entry_period, cfg.exit_period, cfg.trend_period, cfg.atr_period,
    cfg.bb_period, cfg.bb_std, cfg.rsi_period,
    cfg.rsi_long_min, cfg.rsi_long_max, cfg.rsi_short_max, cfg.rsi_short_min,
    cfg.min_atr_pct, cfg.stop_atr_mult, cfg.add_atr_mult, cfg.max_units,
)
n_signal = 0
for s in symbols:
    try:
        rows = client.klines(s, cfg.interval, cfg.kline_lookback)
        closes = [float(r[4]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        sig = strat.evaluate(closes, highs, lows)
        if sig.action in ("LONG", "SHORT"):
            n_signal += 1
        print(f"{s:<14} {sig.action:<6} {sig.reason}")
    except Exception as e:  # noqa: BLE001
        print(f"{s}: ERROR {e}")
print(f"\n可开仓信号数: {n_signal}/{len(symbols)}")
