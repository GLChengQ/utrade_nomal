"""Repair helper: ensure every open position has BOTH stop-loss and take-profit
algo orders, and record both in the bot's state file (so the trailing-stop
logic keeps re-placing them)."""
import json
import config
from binance_futures import BinanceFuturesClient
from quant.indicators import atr

cfg = config.BotConfig.from_env()
client = BinanceFuturesClient(config.API_KEY, config.API_SECRET, config.REST_BASE_URL)

state = json.load(open(config.STATE_PATH, encoding="utf-8"))
positions = [p for p in client.position_risk() if float(p["positionAmt"]) != 0]
existing = client.algo_open_orders()
have_sl, have_tp = {}, {}
for o in existing:
    if o.get("orderType") == "STOP_MARKET":
        have_sl[o["symbol"]] = True
    elif o.get("orderType") == "TAKE_PROFIT_MARKET":
        have_tp[o["symbol"]] = True

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
    dist = a * cfg.stop_atr_mult
    sl = mark - dist if side == "LONG" else mark + dist
    tp = mark + dist * cfg.risk_reward if side == "LONG" else mark - dist * cfg.risk_reward
    close_side = "SELL" if side == "LONG" else "BUY"

    if not have_sl.get(sym):
        client.place_algo_order(sym, close_side, "STOP_MARKET", filters.format_price(sl),
                                close_position=True, working_type=cfg.working_type)
        print(f"  {sym}: 补挂止损 {filters.format_price(sl)}")
    if not have_tp.get(sym):
        client.place_algo_order(sym, close_side, "TAKE_PROFIT_MARKET", filters.format_price(tp),
                                close_position=True, working_type=cfg.working_type)
        print(f"  {sym}: 补挂止盈 {filters.format_price(tp)}")

    st_pos = state.get("positions", {}).get(sym)
    if st_pos:
        st_pos["tp"] = tp
        st_pos["sl"] = sl
        st_pos["peak_price"] = (max(float(st_pos.get("peak_price", mark)), mark) if side == "LONG"
                                else min(float(st_pos.get("peak_price", mark)), mark))

json.dump(state, open(config.STATE_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("repair done")
