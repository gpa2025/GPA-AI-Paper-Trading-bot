"""
SQLite database for historical trading data.

Tables:
  trades            — every BUY/SELL/STOP-LOSS/TAKE-PROFIT/REBALANCE
  portfolio_snapshots — periodic snapshots of total value, cash, positions
  price_history     — price recordings per symbol per cycle
  screener_results  — screener picks with scores
  activity_log      — all bot activity (signals, errors, lifecycle events)
  account_syncs     — records of E*TRADE account syncs

Uses Python's built-in sqlite3 — no extra dependencies.
"""

import sqlite3
import json
import logging
import os
import threading
from datetime import datetime

logger = logging.getLogger("my_logger")

DEFAULT_DB_PATH = "trading_bot.db"


class TradingDatabase:
    """Thread-safe SQLite database for the trading bot."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """One connection per thread (SQLite requirement)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self):
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                action TEXT NOT NULL,
                symbol TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                total REAL NOT NULL,
                cash_after REAL NOT NULL,
                trigger TEXT,
                cycle INTEGER
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                cycle INTEGER,
                cash REAL NOT NULL,
                total_value REAL NOT NULL,
                positions_json TEXT NOT NULL,
                num_positions INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                bid REAL,
                ask REAL,
                volume INTEGER,
                cycle INTEGER
            );

            CREATE TABLE IF NOT EXISTS screener_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                cycle INTEGER,
                results_json TEXT NOT NULL,
                num_results INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                action TEXT NOT NULL,
                message TEXT NOT NULL,
                cycle INTEGER
            );

            CREATE TABLE IF NOT EXISTS account_syncs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                account_id TEXT,
                num_positions INTEGER NOT NULL,
                cash REAL,
                total_value REAL,
                positions_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_action ON trades(action);
            CREATE INDEX IF NOT EXISTS idx_price_history_symbol ON price_history(symbol);
            CREATE INDEX IF NOT EXISTS idx_price_history_timestamp ON price_history(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON portfolio_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp);
        """)
        self._conn.commit()
        logger.info("Database initialized: %s", self.db_path)

    # ------------------------------------------------------------------ #
    #  Trades
    # ------------------------------------------------------------------ #

    def record_trade(self, action: str, symbol: str, qty: int, price: float,
                     cash_after: float, trigger: str = None, cycle: int = None):
        """Record a trade (BUY, SELL, STOP-LOSS, TAKE-PROFIT, REBALANCE)."""
        total = qty * price
        self._conn.execute(
            """INSERT INTO trades (action, symbol, qty, price, total, cash_after, trigger, cycle)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (action, symbol, qty, price, total, cash_after, trigger, cycle),
        )
        self._conn.commit()

    def get_trades(self, limit: int = 100, symbol: str = None,
                   action: str = None, since: str = None) -> list[dict]:
        """Query trade history with optional filters."""
        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if action:
            query += " AND action = ?"
            params.append(action)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_trade_summary(self) -> dict:
        """Get aggregate trade statistics."""
        row = self._conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN action='BUY' THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) as sells,
                SUM(CASE WHEN action='BUY' THEN total ELSE 0 END) as total_bought,
                SUM(CASE WHEN action='SELL' THEN total ELSE 0 END) as total_sold,
                MIN(timestamp) as first_trade,
                MAX(timestamp) as last_trade
            FROM trades
        """).fetchone()
        return dict(row) if row else {}

    # ------------------------------------------------------------------ #
    #  Portfolio snapshots
    # ------------------------------------------------------------------ #

    def record_snapshot(self, cash: float, total_value: float,
                        positions: dict, cycle: int = None):
        """Save a point-in-time portfolio snapshot."""
        self._conn.execute(
            """INSERT INTO portfolio_snapshots (cycle, cash, total_value, positions_json, num_positions)
               VALUES (?, ?, ?, ?, ?)""",
            (cycle, cash, total_value, json.dumps(positions), len(positions)),
        )
        self._conn.commit()

    def get_snapshots(self, limit: int = 500, since: str = None) -> list[dict]:
        """Get portfolio snapshots for charting."""
        query = "SELECT * FROM portfolio_snapshots WHERE 1=1"
        params = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_portfolio_performance(self) -> dict:
        """Get first and latest snapshot for overall performance."""
        first = self._conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp ASC LIMIT 1"
        ).fetchone()
        latest = self._conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if not first or not latest:
            return {}
        first_val = first["total_value"]
        latest_val = latest["total_value"]
        change = latest_val - first_val
        change_pct = (change / first_val * 100) if first_val > 0 else 0
        return {
            "first_snapshot": dict(first),
            "latest_snapshot": dict(latest),
            "total_change": round(change, 2),
            "total_change_pct": round(change_pct, 2),
        }

    # ------------------------------------------------------------------ #
    #  Price history
    # ------------------------------------------------------------------ #

    def record_price(self, symbol: str, price: float, bid: float = None,
                     ask: float = None, volume: int = None, cycle: int = None):
        """Record a price observation."""
        self._conn.execute(
            """INSERT INTO price_history (symbol, price, bid, ask, volume, cycle)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, price, bid, ask, volume, cycle),
        )
        self._conn.commit()

    def get_price_history(self, symbol: str, limit: int = 500,
                          since: str = None) -> list[dict]:
        """Get price history for a symbol."""
        query = "SELECT * FROM price_history WHERE symbol = ?"
        params = [symbol]
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_tracked_symbols(self) -> list[str]:
        """Get all symbols that have price history."""
        rows = self._conn.execute(
            "SELECT DISTINCT symbol FROM price_history ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    # ------------------------------------------------------------------ #
    #  Screener results
    # ------------------------------------------------------------------ #

    def record_screen(self, results: list[dict], cycle: int = None):
        """Save a screener run."""
        self._conn.execute(
            """INSERT INTO screener_results (cycle, results_json, num_results)
               VALUES (?, ?, ?)""",
            (cycle, json.dumps(results), len(results)),
        )
        self._conn.commit()

    def get_screens(self, limit: int = 50) -> list[dict]:
        """Get recent screener runs."""
        rows = self._conn.execute(
            "SELECT * FROM screener_results ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["results"] = json.loads(d.pop("results_json"))
            results.append(d)
        return results

    # ------------------------------------------------------------------ #
    #  Activity log
    # ------------------------------------------------------------------ #

    def record_activity(self, action: str, message: str, cycle: int = None):
        """Log a bot activity event."""
        self._conn.execute(
            """INSERT INTO activity_log (action, message, cycle)
               VALUES (?, ?, ?)""",
            (action, message, cycle),
        )
        self._conn.commit()

    def get_activity(self, limit: int = 200, action: str = None,
                     since: str = None) -> list[dict]:
        """Query activity log."""
        query = "SELECT * FROM activity_log WHERE 1=1"
        params = []
        if action:
            query += " AND action = ?"
            params.append(action)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    #  Account syncs
    # ------------------------------------------------------------------ #

    def record_sync(self, account_id: str, positions: list[dict],
                    cash: float = None, total_value: float = None):
        """Record an E*TRADE account sync."""
        self._conn.execute(
            """INSERT INTO account_syncs
               (account_id, num_positions, cash, total_value, positions_json)
               VALUES (?, ?, ?, ?, ?)""",
            (account_id, len(positions), cash, total_value, json.dumps(positions)),
        )
        self._conn.commit()

    def get_syncs(self, limit: int = 20) -> list[dict]:
        """Get recent sync records."""
        rows = self._conn.execute(
            "SELECT * FROM account_syncs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["positions"] = json.loads(d.pop("positions_json"))
            results.append(d)
        return results

    # ------------------------------------------------------------------ #
    #  Stats / dashboard queries
    # ------------------------------------------------------------------ #

    def get_daily_pnl(self, days: int = 30) -> list[dict]:
        """Get daily P&L from portfolio snapshots (one per day, latest)."""
        rows = self._conn.execute("""
            SELECT
                DATE(timestamp) as date,
                MAX(total_value) as high,
                MIN(total_value) as low,
                -- last snapshot of the day
                (SELECT total_value FROM portfolio_snapshots p2
                 WHERE DATE(p2.timestamp) = DATE(p1.timestamp)
                 ORDER BY p2.timestamp DESC LIMIT 1) as close_value
            FROM portfolio_snapshots p1
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
            LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in rows]

    def get_symbol_trade_stats(self) -> list[dict]:
        """Get per-symbol trade statistics."""
        rows = self._conn.execute("""
            SELECT
                symbol,
                COUNT(*) as num_trades,
                SUM(CASE WHEN action='BUY' THEN qty ELSE 0 END) as shares_bought,
                SUM(CASE WHEN action='SELL' THEN qty ELSE 0 END) as shares_sold,
                SUM(CASE WHEN action='BUY' THEN total ELSE 0 END) as total_bought,
                SUM(CASE WHEN action='SELL' THEN total ELSE 0 END) as total_sold,
                ROUND(SUM(CASE WHEN action='SELL' THEN total ELSE 0 END) -
                      SUM(CASE WHEN action='BUY' THEN total ELSE 0 END), 2) as realized_pnl
            FROM trades
            GROUP BY symbol
            ORDER BY num_trades DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_db_stats(self) -> dict:
        """Get database size and row counts."""
        counts = {}
        for table in ["trades", "portfolio_snapshots", "price_history",
                       "screener_results", "activity_log", "account_syncs"]:
            row = self._conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
            counts[table] = row["c"]
        size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "tables": counts,
            "total_rows": sum(counts.values()),
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / 1024 / 1024, 2),
        }
