from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.core.models import SessionStats, TradeRecord, TradeSignal


class Journal:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT,
                    stopped_at TEXT,
                    start_balance REAL,
                    session_profit REAL,
                    trades_taken INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    stop_reason TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    pair_name TEXT,
                    direction TEXT,
                    stake REAL,
                    expiry TEXT,
                    outcome TEXT,
                    pnl REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    pair_name TEXT,
                    direction TEXT,
                    expiry TEXT,
                    confidence REAL,
                    reason TEXT
                )
                """
            )

    def log_trade(self, trade: TradeRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (timestamp, pair_name, direction, stake, expiry, outcome, pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.timestamp.isoformat(),
                    trade.pair,
                    trade.direction.value,
                    trade.stake,
                    trade.expiry,
                    trade.outcome.value,
                    trade.pnl,
                ),
            )

    def log_session(self, stats: SessionStats) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    started_at, stopped_at, start_balance, session_profit,
                    trades_taken, wins, losses, stop_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stats.started_at.isoformat() if stats.started_at else None,
                    stats.stopped_at.isoformat() if stats.stopped_at else None,
                    stats.start_balance,
                    stats.session_profit,
                    stats.trades_taken,
                    stats.wins,
                    stats.losses,
                    stats.stop_reason.value if stats.stop_reason else None,
                ),
            )

    def log_signal(self, signal: TradeSignal) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO signals (timestamp, pair_name, direction, expiry, confidence, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.timestamp.isoformat(),
                    signal.pair,
                    signal.direction.value,
                    signal.expiry,
                    signal.confidence,
                    signal.reason,
                ),
            )

    def recent_execution_attempts(self, limit: int = 10) -> list[dict[str, str]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, pair_name, direction, expiry, reason
                FROM signals
                WHERE reason LIKE 'execution-attempt%'
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        result: list[dict[str, str]] = []
        for timestamp, pair_name, direction, expiry, reason in rows:
            result.append(
                {
                    "timestamp": str(timestamp or ""),
                    "pair": str(pair_name or ""),
                    "direction": str(direction or ""),
                    "expiry": str(expiry or ""),
                    "reason": str(reason or ""),
                }
            )
        return result
