from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .analysis import AnalysisResult, TechnicalAnalyzer
from .market_data import MarketDataProvider
from .models import Position
from .paper_trading import PaperTradingAccount, TradingStats


class PositionStore:
    def load_positions(self) -> list[Position]:
        raise NotImplementedError

    def save_position(self, position: Position) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class AnalysisReport:
    result: AnalysisResult
    position: Position | None


class MarketBotService:
    def __init__(
        self,
        data_provider: MarketDataProvider,
        *,
        analyzer: TechnicalAnalyzer | None = None,
        account: PaperTradingAccount | None = None,
        store: PositionStore | None = None,
    ) -> None:
        self.data_provider = data_provider
        self.analyzer = analyzer or TechnicalAnalyzer()
        self.account = account or PaperTradingAccount(stake_usd=100.0)
        self.store = store
        if self.store is not None:
            for position in self.store.load_positions():
                self.account.restore_position(position)

    def analyze_and_track(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        limit: int = 200,
    ) -> AnalysisReport:
        candles = self.data_provider.get_candles(symbol, timeframe, limit)
        result = self.analyzer.analyze(symbol, timeframe, candles)
        position = self.account.open_signal(result.signal) if result.signal else None
        if position is not None and self.store is not None:
            self.store.save_position(position)
        return AnalysisReport(result=result, position=position)

    def update_price(
        self,
        symbol: str,
        price: float,
        *,
        observed_at: datetime | None = None,
    ) -> list[Position]:
        closed = self.account.update_price(symbol, price, observed_at=observed_at)
        if self.store is not None:
            for position in closed:
                self.store.save_position(position)
        return closed

    def refresh_open_positions(self) -> list[Position]:
        """Fetch the latest close for every open symbol and close reached targets/stops."""
        closed: list[Position] = []
        requests: set[tuple[str, str]] = {
            (position.signal.symbol, position.signal.timeframe)
            for position in self.open_positions()
        }
        for symbol, timeframe in requests:
            candles = self.data_provider.get_candles(symbol, timeframe, limit=1)
            if candles:
                closed.extend(
                    self.update_price(
                        symbol,
                        candles[-1].close,
                        observed_at=candles[-1].timestamp,
                    )
                )
        return closed

    def stats(self) -> TradingStats:
        return self.account.stats()

    def open_positions(self, *, symbol: str | None = None) -> list[Position]:
        return [position for position in self.account.positions(symbol=symbol) if position.is_open]

    def history(self, *, symbol: str | None = None) -> list[Position]:
        return [position for position in self.account.positions(symbol=symbol) if not position.is_open]
