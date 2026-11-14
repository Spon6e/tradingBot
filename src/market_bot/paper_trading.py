from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import RLock

from .models import Direction, ExitReason, Position, Signal, utc_now


@dataclass(frozen=True)
class TradingStats:
    total_positions: int
    open_positions: int
    closed_positions: int
    winning_positions: int
    losing_positions: int
    flat_positions: int
    win_rate: float
    net_pnl_usd: float
    average_return_pct: float
    profit_factor: float | None


class PaperTradingAccount:
    """Fixed-stake paper trading ledger.

    Every accepted signal allocates exactly ``stake_usd`` of notional value.
    There is intentionally no balance limit: this lets us evaluate each
    forecast independently, even when multiple symbols are tracked at once.
    """

    def __init__(
        self,
        stake_usd: float = 100.0,
        commission_rate: float = 0.0,
        default_timeout: timedelta = timedelta(days=3),
    ) -> None:
        if not isfinite(stake_usd) or stake_usd <= 0:
            raise ValueError("stake_usd must be a finite number greater than zero")
        if not 0 <= commission_rate < 1:
            raise ValueError("commission_rate must be between 0 and 1")
        if default_timeout <= timedelta(0):
            raise ValueError("default_timeout must be positive")

        self.stake_usd = stake_usd
        self.commission_rate = commission_rate
        self.default_timeout = default_timeout
        self._positions: dict[str, Position] = {}
        self._lock = RLock()

    def open_signal(self, signal: Signal, *, opened_at: datetime | None = None) -> Position:
        """Open one virtual $100-style position for a new signal."""
        with self._lock:
            if signal.signal_id in self._positions:
                raise ValueError(f"signal {signal.signal_id} is already tracked")

            opened_at = opened_at or utc_now()
            quantity = self.stake_usd / signal.entry_price
            position = Position(
                signal=signal,
                stake_usd=self.stake_usd,
                quantity=quantity,
                opened_at=opened_at,
            )
            self._positions[signal.signal_id] = position
            return position

    def update_price(
        self,
        symbol: str,
        price: float,
        *,
        observed_at: datetime | None = None,
    ) -> list[Position]:
        """Close positions whose target, stop, or timeout has been reached.

        If a candle crosses both target and stop, the stop is selected because
        it is the conservative assumption when intrabar order is unknown.
        """
        if price <= 0 or not isfinite(price):
            raise ValueError("price must be a finite number greater than zero")
        observed_at = observed_at or utc_now()

        with self._lock:
            closed: list[Position] = []
            for position in self._positions.values():
                if not position.is_open or position.signal.symbol != symbol:
                    continue

                signal = position.signal
                reason: ExitReason | None = None
                exit_price: float | None = None

                if signal.direction is Direction.LONG:
                    if price <= signal.stop_price:
                        reason, exit_price = ExitReason.STOP, signal.stop_price
                    elif price >= signal.target_price:
                        reason, exit_price = ExitReason.TARGET, signal.target_price
                else:
                    if price >= signal.stop_price:
                        reason, exit_price = ExitReason.STOP, signal.stop_price
                    elif price <= signal.target_price:
                        reason, exit_price = ExitReason.TARGET, signal.target_price

                if reason is None and observed_at >= position.opened_at + self.default_timeout:
                    reason, exit_price = ExitReason.TIMEOUT, price

                if reason is not None and exit_price is not None:
                    self._close(position, exit_price, reason, observed_at)
                    closed.append(position)

            return closed

    def close_signal(
        self,
        signal_id: str,
        exit_price: float,
        *,
        reason: ExitReason = ExitReason.MANUAL,
        closed_at: datetime | None = None,
    ) -> Position:
        if exit_price <= 0 or not isfinite(exit_price):
            raise ValueError("exit_price must be a finite number greater than zero")
        with self._lock:
            try:
                position = self._positions[signal_id]
            except KeyError as exc:
                raise KeyError(f"unknown signal {signal_id}") from exc
            if not position.is_open:
                raise ValueError(f"signal {signal_id} is already closed")
            self._close(position, exit_price, reason, closed_at or utc_now())
            return position

    def positions(self, *, symbol: str | None = None) -> list[Position]:
        with self._lock:
            values = list(self._positions.values())
            if symbol is not None:
                values = [item for item in values if item.signal.symbol == symbol]
            return values

    def stats(self) -> TradingStats:
        with self._lock:
            all_positions = list(self._positions.values())
            closed = [item for item in all_positions if not item.is_open]
            pnl_values = [item.pnl_usd for item in closed if item.pnl_usd is not None]
            returns = [item.return_pct for item in closed if item.return_pct is not None]
            winners = [value for value in pnl_values if value > 0]
            losers = [value for value in pnl_values if value < 0]
            flats = len(pnl_values) - len(winners) - len(losers)

            return TradingStats(
                total_positions=len(all_positions),
                open_positions=len(all_positions) - len(closed),
                closed_positions=len(closed),
                winning_positions=len(winners),
                losing_positions=len(losers),
                flat_positions=flats,
                win_rate=len(winners) / len(closed) if closed else 0.0,
                net_pnl_usd=sum(pnl_values),
                average_return_pct=sum(returns) / len(returns) if returns else 0.0,
                profit_factor=(sum(winners) / abs(sum(losers))) if losers else None,
            )

    def _close(
        self,
        position: Position,
        exit_price: float,
        reason: ExitReason,
        closed_at: datetime,
    ) -> None:
        direction_multiplier = 1 if position.signal.direction is Direction.LONG else -1
        gross_pnl = (
            (exit_price - position.signal.entry_price)
            * position.quantity
            * direction_multiplier
        )
        commission = self.commission_rate * position.stake_usd * 2
        pnl = gross_pnl - commission

        position.exit_price = exit_price
        position.exit_reason = reason
        position.closed_at = closed_at
        position.pnl_usd = pnl
        position.return_pct = pnl / position.stake_usd

