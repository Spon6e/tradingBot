from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MarketDataError(RuntimeError):
    """Ошибка получения или разбора рыночных данных."""


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must contain timezone information")
        prices = (self.open, self.high, self.low, self.close, self.volume)
        if any(value <= 0 for value in prices):
            raise ValueError("candle prices and volume must be greater than zero")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle high/low values are inconsistent")


class MarketDataProvider(Protocol):
    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        ...


class InMemoryMarketDataProvider:
    """Источник свечей для тестов и локальной разработки."""

    def __init__(self, candles: dict[tuple[str, str], Sequence[Candle]]) -> None:
        self._candles = {
            (symbol.upper(), timeframe): list(values)
            for (symbol, timeframe), values in candles.items()
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            values = self._candles[(symbol.upper(), timeframe)]
        except KeyError as exc:
            raise MarketDataError(f"Нет свечей для {symbol.upper()} ({timeframe})") from exc
        return values[-limit:]


class BinanceMarketDataProvider:
    """Получает публичные свечи Spot через REST API Binance."""

    BASE_URL = "https://data-api.binance.vision/api/v3/klines"
    TIMEFRAMES = {
        "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
        "12h", "1d", "3d", "1w", "1M",
    }

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        symbol = symbol.upper().strip()
        if not symbol or timeframe not in self.TIMEFRAMES:
            raise ValueError("Укажите корректный символ и таймфрейм Binance")
        if not 1 <= limit <= 1000:
            raise ValueError("Количество свечей должно быть от 1 до 1000")

        query = urlencode({"symbol": symbol, "interval": timeframe, "limit": limit})
        request = Request(
            f"{self.BASE_URL}?{query}",
            headers={"User-Agent": "market-analysis-bot/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise MarketDataError(f"Не удалось получить данные Binance: {exc}") from exc

        if not isinstance(payload, list):
            message = payload.get("msg", "неизвестная ошибка") if isinstance(payload, dict) else payload
            raise MarketDataError(f"Binance вернул ошибку: {message}")

        try:
            return [
                Candle(
                    timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
                for row in payload
            ]
        except (IndexError, TypeError, ValueError) as exc:
            raise MarketDataError("Binance вернул данные свечей в неожиданном формате") from exc


class YahooFinanceMarketDataProvider:
    """Публичный источник дневных и часовых свечей для акций."""

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    TIMEFRAME_CONFIG = {
        "1d": ("1d", "2y"),
        "1h": ("1h", "60d"),
    }

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        symbol = symbol.upper().strip()
        if not symbol or timeframe not in self.TIMEFRAME_CONFIG:
            raise ValueError("Для акций доступны таймфреймы 1d и 1h")
        if not 1 <= limit <= 2000:
            raise ValueError("Количество свечей должно быть от 1 до 2000")

        interval, range_name = self.TIMEFRAME_CONFIG[timeframe]
        query = urlencode({"range": range_name, "interval": interval, "events": "history"})
        request = Request(
            f"{self.BASE_URL}/{symbol}?{query}",
            headers={"User-Agent": "market-analysis-bot/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise MarketDataError(f"Не удалось получить данные акции: {exc}") from exc

        try:
            result = payload["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
            candles = []
            for index, timestamp in enumerate(timestamps):
                values = {key: quote[key][index] for key in ("open", "high", "low", "close", "volume")}
                if any(value is None for value in values.values()):
                    continue
                candles.append(
                    Candle(
                        timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
                        open=float(values["open"]),
                        high=float(values["high"]),
                        low=float(values["low"]),
                        close=float(values["close"]),
                        volume=max(float(values["volume"]), 0.0000001),
                    )
                )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise MarketDataError("Источник акций вернул данные в неожиданном формате") from exc

        if not candles:
            raise MarketDataError(f"Нет доступных свечей для акции {symbol}")
        return candles[-limit:]


class RoutedMarketDataProvider:
    """Выбирает крипто- или фондовый источник по формату тикера."""

    CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "BTC", "ETH")

    def __init__(self, crypto: MarketDataProvider, stocks: MarketDataProvider) -> None:
        self.crypto = crypto
        self.stocks = stocks

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        symbol = symbol.upper().strip()
        provider = self.crypto if symbol.endswith(self.CRYPTO_QUOTES) else self.stocks
        return provider.get_candles(symbol, timeframe, limit)
