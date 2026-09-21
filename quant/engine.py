"""Live trading engine (multi-symbol): data -> signals -> strict execution.

For every symbol in the watchlist, the engine:
- fetches klines and computes the EMA-crossover signal,
- opens positions only inside the trading session and under the concurrent
  position cap,
- on entry immediately places exchange-native protective orders
  (STOP_MARKET stop-loss and TAKE_PROFIT_MARKET take-profit, closePosition=true),
- reconciles closures 24/7 (protective orders live on the exchange).

Risk is shared across symbols: one equity curve, one drawdown/daily circuit
breaker, one concurrent-position limit.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import config as _cfg
from binance_futures import BinanceAPIError, BinanceFuturesClient
from binance_futures.utils import format_step_value

from .db import TradeStore
from .risk import CircuitBreaker, RiskManager
from .session import is_in_session
from .state import StateStore
from .strategy import EMACrossoverStrategy, Signal

log = logging.getLogger("engine")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradingEngine:
    def __init__(
        self,
        client: BinanceFuturesClient,
        strategy: EMACrossoverStrategy,
        risk: RiskManager,
        cfg,
        state_path,
        dry_run: bool = False,
    ):
        self.client = client
        self.strategy = strategy
        self.risk = risk
        self.cfg = cfg
        self.dry_run = dry_run
        self.symbols = list(cfg.symbols)
        self.state = StateStore(state_path)
        self._cooldown: dict[str, float] = {}  # symbol -> cooldown deadline (epoch seconds)
        self.breaker = CircuitBreaker(cfg.max_drawdown_pct, cfg.daily_loss_limit_pct)
        # Optional MySQL history store (never let DB issues break trading).
        try:
            import config as _config
            self.store = TradeStore(
                _config.MYSQL_HOST, _config.MYSQL_PORT, _config.MYSQL_USER,
                _config.MYSQL_PASSWORD, _config.MYSQL_DATABASE,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("MySQL history store unavailable: %s", exc)
            self.store = None
        # Persist breaker state across restarts so a crash can't reset limits.
        st = self.state.data
        if st.get("peak_equity"):
            self.breaker.peak_equity = st["peak_equity"]
        if st.get("daily_date"):
            self.breaker.daily_date = st["daily_date"]
            self.breaker.daily_start_equity = st.get("daily_start_equity")

    # ------------------------------------------------------------------
    # Market / account helpers
    # ------------------------------------------------------------------
    def _klines(self, symbol: str):
        rows = self.client.klines(symbol, self.cfg.interval, self.cfg.kline_lookback)
        closes = [float(r[4]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        return closes, highs, lows

    def _equity(self) -> tuple[float, float]:
        acct = self.client.account()
        # Total equity = realized wallet + unrealized PnL (totalMarginBalance).
        equity = float(acct.get("totalMarginBalance", acct.get("totalWalletBalance", 0.0)))
        return equity, float(acct.get("availableBalance", 0.0))

    def _position(self, symbol: str) -> dict | None:
        rows = self.client.position_risk(symbol)
        if not rows:
            return None
        p = rows[0]
        amt = float(p.get("positionAmt", 0.0))
        return p if amt != 0 else None

    @staticmethod
    def _order_side(side: str) -> str:
        return "BUY" if side == "LONG" else "SELL"

    def _wait_position(self, symbol: str, side: str, tries: int = 4, delay: float = 1.0) -> dict | None:
        for _ in range(tries):
            time.sleep(delay)
            pos = self._position(symbol)
            if pos:
                amt = float(pos["positionAmt"])
                if (side == "LONG" and amt > 0) or (side == "SHORT" and amt < 0):
                    return pos
        return None

    def _wait_flat(self, symbol: str, tries: int = 4, delay: float = 1.0) -> bool:
        for _ in range(tries):
            time.sleep(delay)
            if self._position(symbol) is None:
                return True
        return False

    def _in_session(self) -> bool:
        if not getattr(self.cfg, "session_enabled", True):
            return True
        return is_in_session(
            datetime.now(timezone.utc),
            self.cfg.session_start,
            self.cfg.session_end,
            self.cfg.session_utc_offset,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _cooldown_ok(self, symbol: str) -> bool:
        return time.time() >= self._cooldown.get(symbol, 0.0)

    def _set_cooldown(self, symbol: str, seconds: float = 900.0) -> None:
        self._cooldown[symbol] = time.time() + seconds

    def _open(self, symbol: str, side: str, entry_ref: float, atr_value: float, equity: float) -> bool:
        sl, tp = self.strategy.levels(entry_ref, atr_value, side)
        filters = self.client.symbol_filters(symbol)

        qty = self.risk.position_size(equity, entry_ref, sl, filters.step_size, filters.min_qty)
        if qty <= 0:
            log.warning("%s: position size 0 (risk too small for min lot); skip", symbol)
            return False

        qty_str = format_step_value(qty, filters.step_size)
        sl_str = filters.format_price(sl)
        tp_str = filters.format_price(tp) if tp is not None else None

        # Margin check: skip if we can't afford this position's initial margin.
        margin_needed = (qty * entry_ref) / self.cfg.max_leverage
        _, available = self._equity()
        if available < margin_needed * 1.05:
            log.info("%s insufficient margin (need %.2f avail %.2f); skip", symbol, margin_needed, available)
            self._set_cooldown(symbol)
            return False

        log.info("OPEN %s %s qty=%s entry_ref=%.4f SL=%.4f TP=%s (ATR=%.4f)",
                 symbol, side, qty_str, entry_ref, sl, tp_str or "none", atr_value)

        if self.dry_run:
            log.info("[DRY-RUN] would OPEN %s %s qty=%s SL=%s", symbol, side, qty_str, sl_str)
            self._record_open(symbol, side, qty_str, entry_ref, sl, tp, equity)
            return True

        try:
            self.client.change_leverage(symbol, self.cfg.max_leverage)
        except Exception as exc:
            log.warning("%s set leverage: %s", symbol, exc)

        # entry market order
        try:
            self.client.place_order(symbol, self._order_side(side), "MARKET", quantity=qty_str)
        except BinanceAPIError as exc:
            if exc.code == -2019:
                log.info("%s margin insufficient on entry; skip", symbol)
            else:
                log.error("%s entry rejected: %s", symbol, exc)
            self._set_cooldown(symbol)
            return False

        pos = self._wait_position(symbol, side)
        if pos is None:
            log.error("%s entry did not fill; protective orders NOT placed", symbol)
            self._set_cooldown(symbol)
            return False

        self._place_protective(symbol, side, sl_str, tp_str)
        self._record_open(symbol, side, qty_str, entry_ref, sl, tp, equity)
        return True

    def _place_protective(self, symbol: str, side: str, sl_str: str, tp_str: str | None) -> None:
        """Clear stale algo orders for the symbol, then place stop-loss (and take-profit if any)
        via the Algo Order endpoint (STOP_MARKET / TAKE_PROFIT_MARKET moved off /fapi/v1/order)."""
        close_side = "SELL" if side == "LONG" else "BUY"
        try:
            self.client.cancel_all_algo_orders(symbol)
        except Exception as exc:
            log.warning("%s cancel algo orders: %s", symbol, exc)
        try:
            self.client.place_algo_order(
                symbol, close_side, "STOP_MARKET", sl_str,
                close_position=True, working_type=self.cfg.working_type,
            )
        except Exception as exc:
            log.error("%s STOP-LOSS placement failed: %s", symbol, exc)
        if tp_str is not None:
            try:
                self.client.place_algo_order(
                    symbol, close_side, "TAKE_PROFIT_MARKET", tp_str,
                    close_position=True, working_type=self.cfg.working_type,
                )
            except Exception as exc:
                log.error("%s TAKE-PROFIT placement failed: %s", symbol, exc)

    def _max_units(self) -> int:
        return int(getattr(self.strategy, "max_units", 1))

    def _maybe_add(self, symbol: str, pos_state: dict, atr_value, current_price: float) -> None:
        """Pyramid one unit every `add_atr_mult` * ATR in favour (Turtle)."""
        if not self._cooldown_ok(symbol):
            return
        max_units = self._max_units()
        if max_units <= 1 or not atr_value or atr_value <= 0:
            return
        units = int(pos_state.get("units", 1))
        if units >= max_units:
            return
        add_mult = float(getattr(self.strategy, "add_atr_mult", 0.5))
        last_add = float(pos_state.get("last_add_price", pos_state["entry"]))
        step = atr_value * add_mult
        side = pos_state["side"]
        if side == "LONG" and current_price >= last_add + step:
            self._add_unit(symbol, pos_state, current_price, atr_value)
        elif side == "SHORT" and current_price <= last_add - step:
            self._add_unit(symbol, pos_state, current_price, atr_value)

    def _add_unit(self, symbol: str, pos_state: dict, price: float, atr_value: float) -> None:
        side = pos_state["side"]
        sl, _tp = self.strategy.levels(price, atr_value, side)
        filters = self.client.symbol_filters(symbol)
        equity, available = self._equity()
        qty = self.risk.position_size(equity, price, sl, filters.step_size, filters.min_qty)
        if qty <= 0:
            log.warning("%s add-unit size 0; skip", symbol)
            return
        qty_str = format_step_value(qty, filters.step_size)
        sl_str = filters.format_price(sl)
        # Keep the original take-profit target (don't cancel it on pyramiding).
        tp = pos_state.get("tp")
        tp_str = filters.format_price(tp) if tp is not None else None

        margin_needed = (qty * price) / self.cfg.max_leverage
        if available < margin_needed * 1.05:
            log.info("%s insufficient margin for add (need %.2f avail %.2f); skip", symbol, margin_needed, available)
            self._set_cooldown(symbol)
            return

        new_units = int(pos_state.get("units", 1)) + 1
        log.info("ADD %s %s qty=%s unit=%d/%d newSL=%.4f", symbol, side, qty_str, new_units, self._max_units(), sl)

        if self.dry_run:
            log.info("[DRY-RUN] would ADD %s", symbol)
            self._record_add(symbol, pos_state, price, sl)
            return

        try:
            self.client.place_order(symbol, self._order_side(side), "MARKET", quantity=qty_str)
        except BinanceAPIError as exc:
            if exc.code == -2019:
                log.info("%s margin insufficient on add; skip", symbol)
            else:
                log.error("%s add rejected: %s", symbol, exc)
            self._set_cooldown(symbol)
            return

        self._wait_position(symbol, side)
        self._place_protective(symbol, side, sl_str, tp_str)  # move stop up, keep TP
        self._record_add(symbol, pos_state, price, sl)

    def _update_trailing_stop(self, symbol: str, pos_state: dict, current_price: float, atr_value) -> None:
        """Ratchet the stop-loss in favour of the position as price makes new
        extremes: stop = peak - 2*ATR (long) or trough + 2*ATR (short)."""
        if not atr_value or atr_value <= 0:
            return
        side = pos_state["side"]
        dist = atr_value * self.cfg.stop_atr_mult
        peak = float(pos_state.get("peak_price", pos_state["entry"]))
        if side == "LONG":
            peak = max(peak, current_price)
            new_sl = peak - dist
        else:
            peak = min(peak, current_price)
            new_sl = peak + dist
        pos_state["peak_price"] = peak

        cur_sl = float(pos_state.get("sl", pos_state["entry"]))
        improved = (side == "LONG" and new_sl > cur_sl) or (side == "SHORT" and new_sl < cur_sl)
        if not improved or abs(new_sl - cur_sl) < dist * 0.25:
            return

        filters = self.client.symbol_filters(symbol)
        sl_str = filters.format_price(new_sl)
        tp = pos_state.get("tp")
        tp_str = filters.format_price(tp) if tp is not None else None
        log.info("TRAIL %s %s SL %.4f -> %.4f (peak %.4f)", symbol, side, cur_sl, new_sl, peak)
        pos_state["sl"] = new_sl
        if not self.dry_run:
            self._place_protective(symbol, side, sl_str, tp_str)
        self.state.save()

    def _close(self, symbol: str, side: str, reason: str) -> None:
        log.info("CLOSE %s %s reason=%s", symbol, side, reason)
        if self.dry_run:
            log.info("[DRY-RUN] would CLOSE %s (%s)", symbol, reason)
            return
        close_side = "SELL" if side == "LONG" else "BUY"
        self.client.place_order(symbol, close_side, "MARKET", closePosition="true")
        self._wait_flat(symbol)
        self._record_close(symbol, reason)

    # ------------------------------------------------------------------
    # State recording
    # ------------------------------------------------------------------
    def _record_open(self, symbol, side, qty, entry, sl, tp, equity) -> None:
        self.state.data["positions"][symbol] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "units": 1,
            "entry": entry,
            "last_add_price": entry,
            "peak_price": entry,
            "sl": sl,
            "tp": tp,
            "opened_at": _now_iso(),
            "opened_at_ms": int(time.time() * 1000),
            "entry_equity": equity,
        }
        self.state.save()

    def _record_add(self, symbol, pos_state, price, sl) -> None:
        pos_state["units"] = int(pos_state.get("units", 1)) + 1
        pos_state["last_add_price"] = price
        pos_state["entry"] = price
        pos_state["sl"] = sl
        self.state.save()

    def _realized_pnl_since(self, symbol: str, start_ms: int) -> float | None:
        try:
            rows = self.client.income(symbol, incomeType="REALIZED_PNL", limit=20, startTime=start_ms)
            if rows:
                return sum(float(r["income"]) for r in rows)
        except Exception as exc:
            log.debug("%s income query failed: %s", symbol, exc)
        return None

    def _record_close(self, symbol: str, reason: str) -> None:
        pos = self.state.data["positions"].pop(symbol, None)
        if not pos:
            return
        pnl = self._realized_pnl_since(symbol, pos.get("opened_at_ms", 0))
        if pnl is None:
            equity, _ = self._equity()
            pnl = equity - pos.get("entry_equity", equity)
        close_type = self._infer_close_type(symbol, pos, reason)
        trade = {**pos, "closed_at": _now_iso(), "reason": reason, "close_type": close_type, "pnl": round(pnl, 4)}
        self.state.data["trades"].append(trade)
        self.state.save()
        if self.store is not None:
            self.store.record_trade({
                "symbol": symbol, "side": pos.get("side"), "qty": pos.get("qty"),
                "units": pos.get("units", 1), "entry": pos.get("entry"),
                "sl": pos.get("sl"), "tp": pos.get("tp"), "exit": None,
                "pnl": round(pnl, 4), "reason": reason, "close_type": close_type,
                "opened_at": pos.get("opened_at"), "closed_at": trade["closed_at"],
            })
        log.info("TRADE CLOSED %s pnl=%.2f type=%s reason=%s", symbol, pnl, close_type, reason)

    def _infer_close_type(self, symbol: str, pos: dict, reason: str) -> str:
        """Infer how a position was closed: 止损 / 止盈 / 主动平仓 / 信号平仓."""
        if reason not in ("sl/tp", "sl/tp or manual"):
            return "信号平仓" if reason in ("reverse signal", "exit signal") else reason
        try:
            rows = self.client.mark_price(symbol)
            mark = float(rows[0]["markPrice"]) if isinstance(rows, list) and rows else None
        except Exception:
            mark = None
        if mark is None:
            return "sl/tp"
        sl = float(pos.get("sl", 0.0))
        tp = pos.get("tp")
        side = pos.get("side")
        if side == "LONG" and mark <= sl:
            return "止损"
        if side == "SHORT" and mark >= sl:
            return "止损"
        if tp is not None:
            if side == "LONG" and mark >= float(tp):
                return "止盈"
            if side == "SHORT" and mark <= float(tp):
                return "止盈"
        return "主动平仓"

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------
    def run_cycle(self) -> None:
        equity, _ = self._equity()
        today = datetime.now(timezone.utc).date().isoformat()
        self.state.data["equity_history"].append({"t": _now_iso(), "equity": equity})

        # Reset detection: a sudden large drop in the REALIZED wallet balance
        # that isn't explained by trading is almost always a testnet reset.
        if self.store is not None:
            try:
                acct = self.client.account()
                wallet = float(acct.get("totalWalletBalance", 0.0))
                last_wallet = self.state.data.get("last_wallet")
                if last_wallet and wallet < last_wallet * (1 - _cfg.RESET_DETECT_PCT / 100.0):
                    drop_pct = (last_wallet - wallet) / last_wallet * 100.0
                    self.store.record_reset(
                        prev_wallet=last_wallet, new_wallet=wallet,
                        prev_equity=self.state.data.get("peak_equity", last_wallet),
                        new_equity=equity,
                        note=f"wallet dropped {drop_pct:.1f}% (likely testnet account reset)",
                    )
                    log.warning("ACCOUNT RESET DETECTED: wallet %.2f -> %.2f (%.1f%%)",
                                last_wallet, wallet, drop_pct)
                self.state.data["last_wallet"] = wallet
            except Exception as exc:  # noqa: BLE001
                log.debug("reset detection skipped: %s", exc)

        halt_reason = self.breaker.check(equity, today)
        self.state.data["peak_equity"] = self.breaker.peak_equity
        self.state.data["daily_date"] = self.breaker.daily_date
        self.state.data["daily_start_equity"] = self.breaker.daily_start_equity

        was_halted = bool(self.state.data.get("halted"))
        prev_reason = str(self.state.data.get("halt_reason", ""))
        is_daily = not prev_reason.startswith("max drawdown")

        # Auto-clear daily-loss halts: cooldown elapsed, or condition recovered.
        # (max-drawdown halts stay until manually reset.)
        if was_halted and is_daily:
            tripped_at = self.state.data.get("halted_at") or time.time()
            cooldown_elapsed = (time.time() - tripped_at) >= self.cfg.halt_cooldown_hours * 3600
            condition_cleared = halt_reason is None
            if cooldown_elapsed or condition_cleared:
                self.state.data["halted"] = False
                self.state.data["halt_reason"] = None
                self.state.data["halted_at"] = None
                if cooldown_elapsed:
                    # Re-baseline the daily reference so the new session starts fresh.
                    self.breaker.daily_date = None
                    self.breaker.daily_start_equity = None
                    self.state.data["daily_date"] = None
                    self.state.data["daily_start_equity"] = None
                    halt_reason = None  # ignore the stale breach after re-baseline
                    log.info("circuit breaker cooldown (%.1fh) elapsed — resuming", self.cfg.halt_cooldown_hours)
                else:
                    log.info("circuit breaker cleared — resuming trading")
                was_halted = False

        if halt_reason:
            if not was_halted:
                self.state.data["halted_at"] = time.time()
                log.error("CIRCUIT BREAKER TRIPPED: %s", halt_reason)
            self.state.data["halted"] = True
            self.state.data["halt_reason"] = halt_reason

        halted = bool(self.state.data.get("halted"))
        in_sess = self._in_session()
        positions = self.state.data["positions"]

        # 1) Reconcile closures ALWAYS (24/7) — protective SL orders fire on the
        #    exchange regardless of halt/session state.
        for symbol in list(positions.keys()):
            if self._position(symbol) is None:
                log.info("%s position no longer on exchange (SL/TP or manual)", symbol)
                self._record_close(symbol, "sl/tp")

        # 2) Evaluate signals and manage each watchlist symbol (only when trading).
        if not halted:
            symbols = list(self.symbols)
            random.shuffle(symbols)
            for symbol in symbols:
                closes, highs, lows = self._klines(symbol)
                if len(closes) < self.strategy.warmup + 1:
                    continue

                pos_state = positions.get(symbol)
                signal: Signal = self.strategy.evaluate(
                    closes, highs, lows,
                    pos_state["side"] if pos_state else None,
                )

                # Trailing stop — always ratchet the SL in favour (protective).
                if pos_state:
                    self._update_trailing_stop(symbol, pos_state, float(closes[-1]), signal.atr)

                # Manage existing position (inside session).
                if pos_state and in_sess:
                    if signal.action == "EXIT":
                        self._close(symbol, pos_state["side"], signal.reason or "exit signal")
                        pos_state = None
                    elif signal.action in ("LONG", "SHORT") and signal.action != pos_state["side"]:
                        self._close(symbol, pos_state["side"], "reverse signal")
                        pos_state = None
                    else:
                        self._maybe_add(symbol, pos_state, signal.atr, float(closes[-1]))

                # New entry (inside session, flat, directional signal, under cap).
                if in_sess and pos_state is None and signal.action in ("LONG", "SHORT"):
                    if len(positions) >= self.cfg.max_open_positions:
                        log.info("concurrent position cap (%d) reached; skip %s",
                                 self.cfg.max_open_positions, symbol)
                        continue
                    if not self._cooldown_ok(symbol):
                        continue
                    if signal.atr is None or signal.atr <= 0:
                        log.warning("%s ATR unavailable; skip entry", symbol)
                        continue
                    self._open(symbol, signal.action, float(closes[-1]), signal.atr, equity)

        self.state.save()
        held = {s: p["side"] for s, p in positions.items()}
        log.info("cycle ok | equity=%.2f positions=%d/%d held=%s session=%s halted=%s",
                 equity, len(positions), self.cfg.max_open_positions,
                 held or "none", "IN" if in_sess else "OUT", halted)

    def run(self, once: bool = False) -> None:
        sess = (f"{self.cfg.session_start}~{self.cfg.session_end} UTC+{self.cfg.session_utc_offset}"
                if getattr(self.cfg, "session_enabled", True) else "disabled")
        shown = ",".join(self.symbols[:8]) + (" ..." if len(self.symbols) > 8 else "")
        log.info("Engine started | symbols=%d(%s) interval=%s dry_run=%s session=%s max_positions=%d",
                 len(self.symbols), shown, self.cfg.interval, self.dry_run, sess, self.cfg.max_open_positions)
        while True:
            try:
                self.run_cycle()
            except Exception:
                log.exception("cycle error")
            if once:
                break
            time.sleep(self.cfg.poll_seconds)
