"""H5 dashboard server for the futures trading bot.

Serves a mobile-friendly page plus a JSON summary that combines:
- live Binance account data (balance, positions, unrealized PnL)
- bot state (equity history, closed trades, halt/session status)

Run:  python dashboard/server.py     (binds 0.0.0.0:8080, reachable over Tailscale)

Security: this server has NO authentication — only expose it over your private
Tailscale network, never on the public internet.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the project root importable no matter how this script is launched.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from binance_futures import BinanceFuturesClient
from quant.session import is_in_session

HERE = Path(__file__).resolve().parent
PORT = int(os.getenv("DASH_PORT", "8080"))
CACHE_TTL = 10.0  # seconds to cache the live account snapshot

_client = None
_cache = {"ts": 0.0, "data": None}
_lock = threading.Lock()


def get_client() -> BinanceFuturesClient:
    global _client
    if _client is None:
        _client = BinanceFuturesClient(
            config.API_KEY, config.API_SECRET, config.REST_BASE_URL,
            recv_window=config.RECV_WINDOW, timeout=config.REQUEST_TIMEOUT,
        )
    return _client


def live_account() -> dict:
    """Live account snapshot, cached for CACHE_TTL seconds."""
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["ts"] < CACHE_TTL:
            return _cache["data"]

    c = get_client()
    acct = c.account()
    balances = [b for b in c.balance() if float(b.get("balance", 0.0)) > 0]
    positions = [p for p in c.position_risk() if float(p.get("positionAmt", 0.0)) != 0]

    data = {
        "total_balance": float(acct.get("totalMarginBalance", acct.get("totalWalletBalance", 0.0))),
        "available_balance": float(acct.get("availableBalance", 0.0)),
        "unrealized_pnl": float(acct.get("totalUnrealizedProfit", 0.0)),
        "balances": balances,
        "positions": positions,
    }
    with _lock:
        _cache["ts"] = time.time()
        _cache["data"] = data
    return data


def read_state() -> dict:
    p = config.STATE_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def summary() -> dict:
    cfg = config.BotConfig.from_env()
    if cfg.symbols_auto:
        cfg.symbols = list(config.DEFAULT_SYMBOLS[:cfg.max_symbols])
    acct = live_account()
    st = read_state()
    equity_hist = st.get("equity_history", [])
    last_update = equity_hist[-1]["t"] if equity_hist else None
    in_sess = (is_in_session(datetime.now(timezone.utc),
                             cfg.session_start, cfg.session_end, cfg.session_utc_offset)
               if cfg.session_enabled else True)

    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "env": "TESTNET" if config.TESTNET else "MAINNET",
        "account": acct,
        "bot": {
            "symbols": cfg.symbols,
            "interval": cfg.interval,
            "session_enabled": cfg.session_enabled,
            "session_window": f"{cfg.session_start} ~ {cfg.session_end} (UTC+{cfg.session_utc_offset})",
            "session_in": in_sess,
            "max_open_positions": cfg.max_open_positions,
            "halted": st.get("halted", False),
            "halt_reason": st.get("halt_reason"),
            "tracked_positions": st.get("positions", {}),
            "last_update": last_update,
        },
        "equity_history": equity_hist[-500:],
        "trades": st.get("trades", [])[-50:],
        "daily": {
            "date": st.get("daily_date"),
            "start_equity": st.get("daily_start_equity"),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.startswith("/api/summary"):
            try:
                self._send(200, json.dumps(summary(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                self._send(500, json.dumps({"error": str(exc)}), "application/json; charset=utf-8")
        elif self.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_text(encoding="utf-8")
            self._send(200, html, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # silence per-request logging
        pass


def main() -> None:
    port = int(os.getenv("DASH_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Dashboard running at http://0.0.0.0:{port}")
    print("From your phone (Tailscale): http://<tailscale-ip>:" + str(port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
