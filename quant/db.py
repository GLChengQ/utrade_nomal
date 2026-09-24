"""MySQL persistence for trade history and account-reset events."""
from __future__ import annotations

from datetime import datetime, timedelta

import pymysql


def _to_dt(iso: str | None) -> datetime | None:
    """Convert an ISO UTC timestamp to Beijing time (UTC+8) naive DATETIME."""
    if not iso:
        return None
    s = iso.replace("T", " ").split("+")[0].split(".")[0]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt + timedelta(hours=8)
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
            for col, ddl in [("stop_loss", "DECIMAL(24,8)"), ("take_profit", "DECIMAL(24,8)"),
                             ("close_type", "VARCHAR(32)"), ("margin", "DECIMAL(24,8)"),
                             ("units_detail", "TEXT")]:
                try:
                    cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")
                except Exception:
                    pass  # column already exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS circuit_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    event_at DATETIME,
                    event_type VARCHAR(32),
                    reason VARCHAR(255),
                    equity DECIMAL(24,8),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    day DATE UNIQUE,
                    realized_pnl DECIMAL(24,8),
                    reset_impact DECIMAL(24,8) DEFAULT 0,
                    net_equity DECIMAL(24,8),
                    breaker_count INT DEFAULT 0,
                    reset_count INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            try:
                cur.execute("ALTER TABLE daily_summary ADD COLUMN reset_impact DECIMAL(24,8) DEFAULT 0")
            except Exception:
                pass
            cur.execute("""
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    ts DATETIME,
                    equity DECIMAL(24,8),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_ts (ts)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        c.close()

    def record_trade(self, t: dict) -> None:
        try:
            c = self._connect()
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO trades
                       (symbol, side, qty, units, entry, stop_loss, take_profit, exit_price,
                        pnl, reason, close_type, margin, units_detail, opened_at, closed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (t.get("symbol"), t.get("side"), t.get("qty"), t.get("units", 1),
                     t.get("entry"), t.get("sl"), t.get("tp"), t.get("exit"), t.get("pnl"),
                     t.get("reason"), t.get("close_type"), t.get("margin"),
                     t.get("units_detail"),
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
                for k in ("qty", "units", "entry", "stop_loss", "take_profit", "exit_price", "pnl", "margin"):
                    if r.get(k) is not None:
                        r[k] = float(r[k])
                if r.get("units_detail"):
                    try:
                        import json as _json
                        r["units_detail"] = _json.loads(r["units_detail"])
                    except Exception:
                        r["units_detail"] = []
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

    # ------------------------------------------------------------------
    # Circuit-breaker events + daily summary / net equity
    # ------------------------------------------------------------------
    def record_circuit_event(self, event_type: str, reason: str, equity: float | None = None) -> None:
        """event_type: 'trip' | 'clear' | 'manual_resume'."""
        try:
            c = self._connect()
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO circuit_logs (event_at, event_type, reason, equity) VALUES (NOW(),%s,%s,%s)",
                    (event_type, reason, equity),
                )
            c.close()
        except Exception:
            pass

    def fetch_circuit_logs(self, limit: int = 100) -> list[dict]:
        try:
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM circuit_logs ORDER BY id DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
            c.close()
            for r in rows:
                for k in ("event_at", "created_at"):
                    if r.get(k) is not None:
                        r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
                if r.get("equity") is not None:
                    r["equity"] = float(r["equity"])
            return rows
        except Exception:
            return []

    def rebuild_daily_summary(self) -> None:
        """Recompute daily realized PnL, reset impact, and net equity from
        trades + resets. net_equity = 5000 + cumulative (realized_pnl + reset_impact),
        i.e. the ACTUAL account value including resets."""
        try:
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("DELETE FROM daily_summary")
                cur.execute("""
                    SELECT DATE(closed_at) d, SUM(pnl) pnl
                    FROM trades
                    WHERE closed_at IS NOT NULL
                    GROUP BY DATE(closed_at)
                    ORDER BY d
                """)
                daily = cur.fetchall()
                # reset impact per day = new_wallet - prev_wallet (negative drop)
                cur.execute("""
                    SELECT DATE(detected_at) d, COUNT(*) n, SUM(new_wallet - prev_wallet) impact
                    FROM resets GROUP BY DATE(detected_at)
                """)
                reset_info = {str(r["d"]): (int(r["n"]), float(r["impact"] or 0)) for r in cur.fetchall()}
                cur.execute("SELECT DATE(event_at) d, COUNT(*) n FROM circuit_logs WHERE event_type='trip' GROUP BY DATE(event_at)")
                breaker_counts = {str(r["d"]): int(r["n"]) for r in cur.fetchall()}

            # Build a combined per-day map so reset-only days also appear.
            day_map = {}
            for row in daily:
                day_map[str(row["d"])] = float(row["pnl"] or 0)
            for d in reset_info:
                day_map.setdefault(d, 0.0)

            net = 5000.0
            for day in sorted(day_map):
                realized = day_map[day]
                rn, impact = reset_info.get(day, (0, 0.0))
                net += realized + impact
                with c.cursor() as cur:
                    cur.execute(
                        """INSERT INTO daily_summary (day, realized_pnl, reset_impact, net_equity, breaker_count, reset_count)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (day, round(realized, 4), round(impact, 4), round(net, 4),
                         breaker_counts.get(day, 0), rn),
                    )
            c.close()
        except Exception:
            pass

    def fetch_daily_summary(self, year: int, month: int) -> list[dict]:
        try:
            self.rebuild_daily_summary()
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM daily_summary WHERE DATE_FORMAT(day,'%%Y-%%m')=%s ORDER BY day",
                    (f"{year:04d}-{month:02d}",),
                )
                rows = cur.fetchall()
            c.close()
            for r in rows:
                if r.get("day") is not None:
                    r["day"] = str(r["day"])
                if r.get("created_at") is not None:
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                for k in ("realized_pnl", "reset_impact", "net_equity"):
                    if r.get(k) is not None:
                        r[k] = float(r[k])
            return rows
        except Exception:
            return []

    def fetch_net_equity(self, limit: int = 2000) -> list[dict]:
        """Cumulative net equity curve (5000 + cumulative realized PnL), which
        excludes resets."""
        try:
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("""
                    SELECT closed_at, pnl FROM trades WHERE closed_at IS NOT NULL ORDER BY id ASC
                """)
                rows = cur.fetchall()
            c.close()
            net = 5000.0
            out = []
            for r in rows:
                net += float(r["pnl"] or 0)
                ts = r["closed_at"]
                out.append({"t": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "", "equity": round(net, 4)})
            return out[-limit:]
        except Exception:
            return []

    def record_equity(self, equity: float) -> None:
        try:
            c = self._connect()
            with c.cursor() as cur:
                cur.execute("INSERT INTO equity_snapshots (ts, equity) VALUES (NOW(), %s)", (equity,))
            c.close()
        except Exception:
            pass

    def fetch_equity_history(self, days: int = 7, max_points: int = 600) -> list[dict]:
        """Equity snapshots for the last `days`, down-sampled to ~max_points."""
        try:
            c = self._connect()
            with c.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT ts, equity FROM equity_snapshots WHERE ts >= NOW() - INTERVAL %s DAY ORDER BY ts ASC",
                    (days,),
                )
                rows = cur.fetchall()
            c.close()
            out = [{"t": r["ts"].strftime("%Y-%m-%d %H:%M:%S"), "equity": float(r["equity"])} for r in rows]
            if len(out) > max_points:
                step = len(out) / max_points
                out = [out[int(i * step)] for i in range(max_points)]
            return out
        except Exception:
            return []
