"""Central configuration, loaded from environment variables / `.env`.

Sensitive values (API key / secret) live only in `.env` (gitignored) or in
real environment variables. This module never hardcodes credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None else default


def _get_float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v is not None else default


def _get_symbols(name: str, default: list) -> list:
    v = os.getenv(name)
    if v:
        return [s.strip().upper() for s in v.split(",") if s.strip()]
    return list(default)


# Curated watchlist — established, liquid USDⓈ-M perpetuals (all on testnet).
# Used as the "auto" universe; `BOT_MAX_SYMBOLS` takes the first N of these.
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "LTCUSDT", "BCHUSDT", "UNIUSDT", "ATOMUSDT", "ETCUSDT",
    "XLMUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "SUIUSDT", "NEARUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT",
    "AAVEUSDT", "TRXUSDT", "TONUSDT", "RUNEUSDT", "CRVUSDT",
    "LDOUSDT", "RENDERUSDT", "JUPUSDT", "PYTHUSDT", "STRKUSDT",
    "ENAUSDT", "WLDUSDT", "ORDIUSDT", "SANDUSDT", "MANAUSDT",
    "GALAUSDT", "AXSUSDT", "FETUSDT", "ICPUSDT", "1000PEPEUSDT",
    "1000SHIBUSDT", "WIFUSDT", "1000BONKUSDT", "TAOUSDT", "HBARUSDT",
]


# Credentials
API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")

# Keep `true` while using the testnet keys above. Set `false` only after you
# have a separate mainnet key and understand the live-money risk.
TESTNET = _as_bool(os.getenv("BINANCE_TESTNET"), default=True)

# Request tuning
RECV_WINDOW = int(os.getenv("BINANCE_RECV_WINDOW", "5000"))
REQUEST_TIMEOUT = float(os.getenv("BINANCE_REQUEST_TIMEOUT", "10"))

# USDⓈ-M Futures REST / WebSocket base URLs
if TESTNET:
    REST_BASE_URL = "https://testnet.binancefuture.com"
    WS_BASE_URL = "wss://stream.binancefuture.com"
else:
    REST_BASE_URL = "https://fapi.binance.com"
    WS_BASE_URL = "wss://fstream.binance.com"


# ----------------------------------------------------------------------
# Strategy / risk / engine configuration
# ----------------------------------------------------------------------
@dataclass
class BotConfig:
    """All knobs for the quant strategy, risk controls, and engine loop.

    Every field can be overridden with an environment variable prefixed by
    `BOT_` (e.g. BOT_SYMBOL, BOT_EMA_FAST, BOT_RISK_PER_TRADE).
    """
    # Market
    symbols: list = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    symbols_auto: bool = True            # auto-select top `max_symbols` liquid perps
    max_symbols: int = 40                # watchlist size when symbols_auto=True
    interval: str = "15m"                # shorter bars -> more signals per day
    kline_lookback: int = 400            # enough history for 20-bar breakout + ATR
    poll_seconds: int = 60               # poll interval (keep low to avoid IP rate-limit bans)

    # Strategy selector
    strategy_type: str = "vgas"          # "vgas" | "turtle" | "ema"

    # Shared
    atr_period: int = 20                 # ATR lookback (EMA stop / Turtle N)

    # EMA strategy (strategy_type == "ema")
    ema_fast: int = 9
    ema_slow: int = 55
    trend_filter_period: int = 100        # longer EMA regime filter (0 = off)
    atr_multiplier: float = 2.0          # stop distance = ATR * multiplier
    risk_reward: float = 2.0             # take profit = stop distance * risk_reward

    # Turtle strategy (strategy_type == "turtle")
    entry_period: int = 15               # Donchian entry breakout (bars)
    exit_period: int = 10                # Donchian exit breakout (bars)
    stop_atr_mult: float = 2.0           # stop = 2 * N
    add_atr_mult: float = 0.5            # pyramid every 0.5 * N
    max_units: int = 3                   # max pyramid units per position

    # VGAS strategy (strategy_type == "vgas") — turtle breakout + filters
    trend_period: int = 55               # slow EMA for trend direction
    bb_period: int = 20                  # Bollinger period
    bb_std: float = 2.0                  # Bollinger std dev
    rsi_period: int = 14                 # RSI period
    rsi_long_min: float = 50.0           # long entry RSI floor
    rsi_long_max: float = 80.0           # long entry RSI ceiling (avoid overbought)
    rsi_short_max: float = 50.0          # short entry RSI ceiling
    rsi_short_min: float = 20.0          # short entry RSI floor (avoid oversold)
    min_atr_pct: float = 0.001           # min ATR/price volatility (0.1%)

    working_type: str = "MARK_PRICE"     # MARK_PRICE | CONTRACT_PRICE

    # Risk management (STRICT safety net — never disabled)
    risk_per_trade: float = 0.015        # risk per unit (1.5% — bolder, still bounded)
    max_leverage: int = 5                # leverage set on the symbol
    max_position_pct: float = 0.5        # max notional = equity * pct * leverage
    max_open_positions: int = 10         # concurrent positions across all symbols (bold)
    max_drawdown_pct: float = 0.15       # halt trading after 15% equity drawdown
    daily_loss_limit_pct: float = 0.03   # halt for the day after 3% daily loss

    # Execution
    dry_run: bool = False                # True = simulate decisions, no orders

    # Trading session (China Standard Time = UTC+8, no DST)
    session_enabled: bool = True         # only open new trades inside the window
    session_start: str = "09:00"
    session_end: str = "01:00"           # next day (crosses midnight)
    session_utc_offset: int = 8          # +8 = China Standard Time

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            symbols=_get_symbols("BOT_SYMBOLS", DEFAULT_SYMBOLS),
            symbols_auto=_as_bool(os.getenv("BOT_SYMBOLS_AUTO"), default=True),
            max_symbols=_get_int("BOT_MAX_SYMBOLS", 40),
            interval=_get_str("BOT_INTERVAL", "15m"),
            kline_lookback=_get_int("BOT_KLINE_LOOKBACK", 400),
            poll_seconds=_get_int("BOT_POLL_SECONDS", 60),
            strategy_type=_get_str("BOT_STRATEGY", "vgas"),
            atr_period=_get_int("BOT_ATR_PERIOD", 20),
            ema_fast=_get_int("BOT_EMA_FAST", 9),
            ema_slow=_get_int("BOT_EMA_SLOW", 55),
            trend_filter_period=_get_int("BOT_TREND_FILTER_PERIOD", 100),
            atr_multiplier=_get_float("BOT_ATR_MULTIPLIER", 2.0),
            risk_reward=_get_float("BOT_RISK_REWARD", 2.0),
            entry_period=_get_int("BOT_ENTRY_PERIOD", 15),
            exit_period=_get_int("BOT_EXIT_PERIOD", 10),
            stop_atr_mult=_get_float("BOT_STOP_ATR_MULT", 2.0),
            add_atr_mult=_get_float("BOT_ADD_ATR_MULT", 0.5),
            max_units=_get_int("BOT_MAX_UNITS", 3),
            trend_period=_get_int("BOT_TREND_PERIOD", 55),
            bb_period=_get_int("BOT_BB_PERIOD", 20),
            bb_std=_get_float("BOT_BB_STD", 2.0),
            rsi_period=_get_int("BOT_RSI_PERIOD", 14),
            rsi_long_min=_get_float("BOT_RSI_LONG_MIN", 50.0),
            rsi_long_max=_get_float("BOT_RSI_LONG_MAX", 80.0),
            rsi_short_max=_get_float("BOT_RSI_SHORT_MAX", 50.0),
            rsi_short_min=_get_float("BOT_RSI_SHORT_MIN", 20.0),
            min_atr_pct=_get_float("BOT_MIN_ATR_PCT", 0.001),
            working_type=_get_str("BOT_WORKING_TYPE", "MARK_PRICE"),
            risk_per_trade=_get_float("BOT_RISK_PER_TRADE", 0.015),
            max_leverage=_get_int("BOT_MAX_LEVERAGE", 5),
            max_position_pct=_get_float("BOT_MAX_POSITION_PCT", 0.5),
            max_open_positions=_get_int("BOT_MAX_OPEN_POSITIONS", 10),
            max_drawdown_pct=_get_float("BOT_MAX_DRAWDOWN_PCT", 0.15),
            daily_loss_limit_pct=_get_float("BOT_DAILY_LOSS_LIMIT_PCT", 0.03),
            dry_run=_as_bool(os.getenv("BOT_DRY_RUN"), default=False),
            session_enabled=_as_bool(os.getenv("BOT_SESSION_ENABLED"), default=True),
            session_start=_get_str("BOT_SESSION_START", "09:00"),
            session_end=_get_str("BOT_SESSION_END", "01:00"),
            session_utc_offset=_get_int("BOT_SESSION_UTC_OFFSET", 8),
        )


# Paths (relative to project root)
STATE_PATH = BASE_DIR / "state" / "trading_state.json"
LOG_PATH = BASE_DIR / "logs" / "bot.log"
