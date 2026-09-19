"""One-off: place fresh 2*ATR stop-loss algo orders for every open position
(used after the SL-placement bug fix to re-protect existing positions)."""
import config
from binance_futures import BinanceFuturesClient
from quant.indicators import atr

cfg = config.BotConfig.from_env()
client = BinanceFuturesClient(config.API_KEY, config.API_SECRET, config.REST_BASE_URL)

positions = [p for p in client.position_risk() if float(p["positionAmt"]) != 0]
print(f"open positions: {len(positions)}")
for p in positions:
    sym = p["symbol"]
    amt = float(p["positionAmt"])
    side = "LONG" if amt > 0 else "SHORT"
    rows = client.klines(sym, cfg.interval, 150)
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    a = atr(highs, lows, closes, cfg.atr_period)[-1]
    mark = float(p["markPrice"]) or closes[-1]
    filters = client.symbol_filters(sym)
    sl = mark - a * cfg.stop_atr_mult if side == "LONG" else mark + a * cfg.stop_atr_mult
    sl_str = filters.format_price(sl)
    close_side = "SELL" if side == "LONG" else "BUY"
    try:
        r = client.place_algo_order(sym, close_side, "STOP_MARKET", sl_str,
                                    close_position=True, working_type=cfg.working_type)
        print(f"  {sym:<12} {side:<6} mark={mark:<10} SL={sl_str:<10} -> algoId={r.get('algoId')}")
    except Exception as e:  # noqa: BLE001
        print(f"  {sym:<12} {side:<6} FAILED: {e}")
