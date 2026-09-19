"""Quantitative trading engine: indicators, strategy, risk, backtest."""
from .strategy import EMACrossoverStrategy, Signal, TurtleStrategy, VGASStrategy
from .risk import RiskManager
from .engine import TradingEngine
from .backtest import run_backtest, summarize

__all__ = [
    "EMACrossoverStrategy",
    "TurtleStrategy",
    "VGASStrategy",
    "Signal",
    "RiskManager",
    "TradingEngine",
    "run_backtest",
    "summarize",
]
