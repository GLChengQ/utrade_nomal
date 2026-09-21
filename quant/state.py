"""Persistent state: open position, trade log, equity history, halt flags."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _default() -> dict:
    return {
        "positions": {},              # symbol -> {symbol, side, qty, entry, sl, tp, opened_at_ms, entry_equity}
        "trades": [],                 # closed trades (append only)
        "equity_history": [],         # [{t, equity}] trimmed to last N entries
        "peak_equity": 0.0,
        "daily_date": None,
        "daily_start_equity": 0.0,
        "halted": False,
        "halt_reason": None,
        "halted_at": None,             # epoch seconds when the breaker tripped
        "last_wallet": None,           # last observed realized wallet balance (reset detection)
    }


class StateStore:
    def __init__(self, path: str | Path, max_equity_history: int = 10000):
        self.path = Path(path)
        self.max_equity_history = max_equity_history
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                merged = _default()
                merged.update(loaded)
                # Migrate the old single-position format to the multi-symbol dict.
                if merged.get("position") is not None:
                    old = merged.pop("position")
                    if old.get("symbol"):
                        merged["positions"][old["symbol"]] = old
                return merged
            except (json.JSONDecodeError, OSError):
                return _default()
        return _default()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Trim equity history so the file stays small.
        if len(self.data["equity_history"]) > self.max_equity_history:
            self.data["equity_history"] = self.data["equity_history"][-self.max_equity_history:]
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
