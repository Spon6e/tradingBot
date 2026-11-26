from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from threading import RLock

from .analysis import AnalysisResult, TechnicalAnalyzer
from .market_data import (
    Candle,
    MarketDataError,
    MarketDataProvider,
    is_crypto,
    normalize_symbol,
    timeframe_delta,
)
from .models import Position, utc_now
from .paper_trading import PaperTradingAccount, TradingStats
from .storage import SQLitePositionStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisReport:
    result: AnalysisResult
    position: Position | None
    candles: tuple[Candle, ...]
    created: bool = False


class MarketBotService:
    def __init__(
        self,
        data_provider: MarketDataProvider,
        *,
        analyzer: TechnicalAnalyzer | None = None,
        account: PaperTradingAccount | None = None,
        store: SQLitePositionStore | None = None,
    ) -> None:
        self.data_provider = data_provider
        self.analyzer = analyzer or TechnicalAnalyzer()
        self.account = account or PaperTradingAccount(stake_usd=100.0)
        self.store = store
        self._lock = RLock()
        self.last_refresh: datetime | None = None
        self.refresh_errors: dict[str, str] = {}
        if self.store:
            for position in self.store.load_positions():
                self.account.restore_position(position)

    def _save(self, position: Position) -> None:
        if self.store:
            try:
                self.store.save_position(position)
            except Exception:
                self.account = PaperTradingAccount(
                    stake_usd=self.account.stake_usd,
                    commission_rate=self.account.commission_rate,
                    default_timeout=self.account.default_timeout,
                )
                for item in self.store.load_positions():
                    self.account.restore_position(item)
                raise

    def analyze_and_track(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        limit: int = 200,
        chat_id: int = 0,
        now: datetime | None = None,
    ) -> AnalysisReport:
        with self._lock:
            now = now or utc_now()
            symbol = normalize_symbol(symbol)
            timeframe_delta(timeframe)
            candles = self.data_provider.get_candles(symbol, timeframe, limit)
            candles = [c for c in candles if c.end(timeframe) <= now]
            result = self.analyzer.analyze(symbol, timeframe, candles)
            position = None
            created = False
            if result.signal:
                key = f"{chat_id}:{symbol}:{timeframe}:{candles[-1].timestamp.isoformat()}"
                signal = replace(result.signal, signal_id=sha256(key.encode()).hexdigest())
                result = replace(result, signal=signal)
                position = next(
                    (p for p in self.account.positions() if p.signal.signal_id == signal.signal_id),
                    None,
                )
                active = next(
                    (
                        p
                        for p in self.open_positions(chat_id=chat_id)
                        if p.signal.symbol == symbol and p.signal.timeframe == timeframe
                    ),
                    None,
                )
                position = position or active
                max_age = timeframe_delta(timeframe) * 2 if is_crypto(symbol) else timedelta(days=4)
                fresh = now - candles[-1].end(timeframe) <= max_age
                if position is None and fresh:
                    position = self.account.open_signal(
                        signal, opened_at=now, owner_chat_id=chat_id, pending=True
                    )
                    self._save(position)
                    created = True
            return AnalysisReport(result, position, tuple(candles), created)

    def update_price(
        self, symbol: str, price: float, *, observed_at: datetime | None = None
    ) -> list[Position]:
        with self._lock:
            closed = self.account.update_price(symbol, price, observed_at=observed_at)
            for position in closed:
                self._save(position)
            return closed

    def refresh_open_positions(self) -> list[Position]:
        with self._lock:
            self.refresh_errors = {}
            closed = []
            groups = {(p.signal.symbol, p.signal.timeframe) for p in self.open_positions()}
            for symbol, timeframe in sorted(groups):
                try:
                    candles = self.data_provider.get_candles(symbol, timeframe, 999)
                    for position in self.open_positions(symbol=symbol):
                        if position.signal.timeframe != timeframe:
                            continue
                        cursor = position.last_candle_at or position.opened_at
                        if (
                            candles
                            and candles[0].timestamp > cursor + timeframe_delta(timeframe) * 5
                        ):
                            raise MarketDataError(
                                "Недостаточно истории после простоя; результат не обновлён"
                            )
                        for candle in candles:
                            if candle.end(timeframe) > utc_now():
                                continue
                            before = (position.last_candle_at, position.pending)
                            did_close = self.account.process_candle(position, candle)
                            if (position.last_candle_at, position.pending) != before or did_close:
                                self._save(position)
                            if did_close:
                                closed.append(position)
                                break
                except (MarketDataError, ValueError) as exc:
                    self.refresh_errors[f"{symbol} {timeframe}"] = str(exc)
                    logger.warning("Обновление %s %s: %s", symbol, timeframe, exc)
            self.last_refresh = utc_now()
            return closed

    def positions(self, chat_id: int | None = None) -> list[Position]:
        with self._lock:
            return [
                p for p in self.account.positions() if chat_id is None or p.owner_chat_id == chat_id
            ]

    def stats(self, chat_id: int | None = None) -> TradingStats:
        with self._lock:
            return self.account.stats(owner_chat_id=chat_id)

    def open_positions(
        self, *, symbol: str | None = None, chat_id: int | None = None
    ) -> list[Position]:
        return [
            p
            for p in self.positions(chat_id)
            if p.is_open and (symbol is None or p.signal.symbol == symbol)
        ]

    def history(self, *, chat_id: int | None = None) -> list[Position]:
        return [p for p in self.positions(chat_id) if not p.is_open]

    def pending_notifications(self) -> list[Position]:
        return [p for p in self.positions() if not p.is_open and not p.notified and p.owner_chat_id]

    def mark_notified(self, signal_id: str) -> None:
        with self._lock:
            position = next(p for p in self.positions() if p.signal.signal_id == signal_id)
            position.notified = True
            self._save(position)

    def watches(self, chat_id: int | None = None) -> list[tuple[int, str, str]]:
        return self.store.watches(chat_id) if self.store else []

    def watch(self, chat_id: int, symbol: str, timeframe: str) -> None:
        with self._lock:
            if not self.store:
                raise ValueError("Для наблюдения требуется SQLite")
            symbol = normalize_symbol(symbol)
            timeframe_delta(timeframe)
            if (chat_id, symbol, timeframe) in self.watches(chat_id):
                return
            if len(self.watches(chat_id)) >= 10 or len(self.watches()) >= 100:
                raise ValueError("Лимит наблюдения: 10 тикеров на пользователя, 100 на бота")
            self.data_provider.get_candles(symbol, timeframe, 60)
            self.store.add_watch(chat_id, symbol, timeframe)

    def unwatch(self, chat_id: int, symbol: str, timeframe: str) -> None:
        if self.store:
            self.store.remove_watch(chat_id, normalize_symbol(symbol), timeframe)

    def scan_watches(self) -> list[AnalysisReport]:
        reports = []
        for chat_id, symbol, timeframe in self.watches():
            try:
                report = self.analyze_and_track(symbol, timeframe, chat_id=chat_id)
                if report.created:
                    reports.append(report)
            except (MarketDataError, ValueError) as exc:
                logger.warning("Наблюдение %s: %s", symbol, exc)
        return reports
