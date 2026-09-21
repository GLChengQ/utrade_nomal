"""MySQL persistence for trade history and account-reset events."""
from __future__ import annotations

from datetime import datetime

import pymysql


def _to_dt(iso: str | None) -> datetime | None:
    """Convert an ISO timestamp like '2026-09-21T08:37:24.875+00:00' to a naive
    DATETIME '2026-09-21 08:37:24' (UTC)."""
    if not iso:
        return None
    s = iso.replace("T", " ").split("+")[0].split(".")[0]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class TradeStore:
    """Stores closed trades and reset events in MySQL."""

    def __init__(self, host, port, user, password, database):
        self.params = dict(host=host, port=int(port), user=user, password=password,
                           database=database, charset="utf8mb4")
        self._ensure_schema()

    def _connect(self):
        return pymysql.connect(**self.params, autocommit=True)

    def _ensure_schema(self):
        base = dict(self.params)
        base.pop("database")
        c = pymysql.connect(**base, autocommit=True)
        with c.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.params['database']}` CHARACTER SET utf8mb4")
        c.close()

        c = self._connect()
        with c.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(32) NOT NULL,
                    side VARCHAR(8),
                    qty DECIMAL(24,8),
                    units INT DEFAULT 1,
                    entry DECIMAL(24,8),
                    stop_loss DECIMAL(24,8),
                    take_profit DECIMAL(24,8),
                    exit_price DECIMAL(24,8),
                    pnl DECIMAL(24,8),
                    reason VARCHAR(64),
                    close_type VARCHAR(32),
                    opened_at DATETIME,
                    closed_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_closed_at (closed_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS resets (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    detected_at DATETIME,
                    prev_wallet DECIMAL(24,8),
                    new_wallet DECIMAL(24,8),
                    prev_equity DECIMAL(24,8),
                    new_equity DECIMAL(24,8),
                    note VARCHAR(255),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # Migrate the existing `trades` table if it was created before these columns existed.
            for col, ddl in [("stop_loss", "DECIMAL(24,8)"), ("take_profit", "DECIMAL(24,8)"), ("close_type", "VARCHAR(32)")]:
                try:
                    cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")
                except Exception:
                    pass  # column already exists
        c.close()

    def record_trade(self, t: dict) -> None:
        try:
            c = self._connect()
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO trades
                       (symbol, side, qty, units, entry, stop_loss, take_profit, exit_price,
                        pnl, reason, close_type, opened_at, closed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (t.get("symbol"), t.get("side"), t.get("qty"), t.get("units", 1),
                     t.get("entry"), t.get("sl"), t.get("tp"), t.get("exit"), t.get("pnl"),
                     t.get("reason"), t.get("close_type"),
                     _to_dt(t.get("opened_at")), _to_dt(t.get("closed_at"))),
                )
            c.close()
        except Exception:
            # Never let DB issues break trading.
            pass

    def record_reset(self, prev_wallet, new_wallet, prev_equity, new_equity, note="") -> None:
        try:
            c = self._connect()
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO resets
                       (detected_at, prev_wallet, new_wallet, prev_equity, new_equity, note)
                       VALUES (NOW(), %s,%s,%s,%s,%s)""",
                    (prev_wallet, new_wallet, prev_equity, new_equity, note),
                )
            c.close()
        except Exception:
            pass

    def fetch_trades(self, limit: int = 200) -> list[dict]:
        try:
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
            c.close()
            for r in rows:
                for k in ("closed_at", "opened_at", "created_at"):
                    if r.get(k) is not None:
                        r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
                for k in ("qty", "units", "entry", "stop_loss", "take_profit", "exit_price", "pnl"):
                    if r.get(k) is not None:
                        r[k] = float(r[k])
            return rows
        except Exception:
            return []

    def fetch_resets(self, limit: int = 50) -> list[dict]:
        try:
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM resets ORDER BY id DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
            c.close()
            for r in rows:
                for k in ("detected_at", "created_at"):
                    if r.get(k) is not None:
                        r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
                for k in ("prev_wallet", "new_wallet", "prev_equity", "new_equity"):
                    if r.get(k) is not None:
                        r[k] = float(r[k])
            return rows
        except Exception:
            return []
