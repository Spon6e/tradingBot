from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from .models import Direction, ExitReason, Position, Signal


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text_to_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class SQLitePositionStore:
    """SQLite-хранилище прогнозов и результатов paper trading."""

    def __init__(self, path: str | Path = "market_bot.sqlite3") -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    signal_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    timeframe TEXT NOT NULL,
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    stake_usd REAL NOT NULL,
                    quantity REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    exit_price REAL,
                    exit_reason TEXT,
                    closed_at TEXT,
                    pnl_usd REAL,
                    return_pct REAL
                )
                """
            )

    def save_position(self, position: Position) -> None:
        signal = position.signal
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO positions (
                    signal_id, symbol, direction, entry_price, target_price, stop_price,
                    timeframe, confidence, created_at, stake_usd, quantity, opened_at,
                    exit_price, exit_reason, closed_at, pnl_usd, return_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    exit_price = excluded.exit_price,
                    exit_reason = excluded.exit_reason,
                    closed_at = excluded.closed_at,
                    pnl_usd = excluded.pnl_usd,
                    return_pct = excluded.return_pct
                """,
                (
                    signal.signal_id,
                    signal.symbol,
                    signal.direction.value,
                    signal.entry_price,
                    signal.target_price,
                    signal.stop_price,
                    signal.timeframe,
                    signal.confidence,
                    _datetime_to_text(signal.created_at),
                    position.stake_usd,
                    position.quantity,
                    _datetime_to_text(position.opened_at),
                    position.exit_price,
                    position.exit_reason.value if position.exit_reason else None,
                    _datetime_to_text(position.closed_at),
                    position.pnl_usd,
                    position.return_pct,
                ),
            )

    def load_positions(self) -> list[Position]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM positions ORDER BY opened_at ASC"
            ).fetchall()
        return [self._row_to_position(row) for row in rows]

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        signal = Signal(
            symbol=row["symbol"],
            direction=Direction(row["direction"]),
            entry_price=row["entry_price"],
            target_price=row["target_price"],
            stop_price=row["stop_price"],
            timeframe=row["timeframe"],
            confidence=row["confidence"],
            created_at=_text_to_datetime(row["created_at"]),
            signal_id=row["signal_id"],
        )
        return Position(
            signal=signal,
            stake_usd=row["stake_usd"],
            quantity=row["quantity"],
            opened_at=_text_to_datetime(row["opened_at"]),
            exit_price=row["exit_price"],
            exit_reason=ExitReason(row["exit_reason"]) if row["exit_reason"] else None,
            closed_at=_text_to_datetime(row["closed_at"]),
            pnl_usd=row["pnl_usd"],
            return_pct=row["return_pct"],
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLitePositionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
