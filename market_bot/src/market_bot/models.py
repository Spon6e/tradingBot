from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    TARGET = "target"
    STOP = "stop"
    TIMEOUT = "timeout"
    MANUAL = "manual"


@dataclass(frozen=True)
class Signal:
    """A market forecast that can be tracked as a paper position."""

    symbol: str
    direction: Direction
    entry_price: float
    target_price: float
    stop_price: float
    timeframe: str = "1h"
    confidence: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    signal_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        for name, value in (
            ("entry_price", self.entry_price),
            ("target_price", self.target_price),
            ("stop_price", self.stop_price),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")

        if self.direction is Direction.LONG:
            if self.target_price <= self.entry_price:
                raise ValueError("LONG target_price must be above entry_price")
            if self.stop_price >= self.entry_price:
                raise ValueError("LONG stop_price must be below entry_price")
        else:
            if self.target_price >= self.entry_price:
                raise ValueError("SHORT target_price must be below entry_price")
            if self.stop_price <= self.entry_price:
                raise ValueError("SHORT stop_price must be above entry_price")

        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


@dataclass
class Position:
    signal: Signal
    stake_usd: float
    quantity: float
    opened_at: datetime
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    closed_at: datetime | None = None
    pnl_usd: float | None = None
    return_pct: float | None = None

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

