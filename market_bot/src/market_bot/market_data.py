from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Protocol, Sequence


class MarketDataError(RuntimeError):
    """Источник недоступен или вернул неполные данные."""


TIMEFRAMES = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def timeframe_delta(timeframe: str) -> timedelta:
    if timeframe not in TIMEFRAMES:
        raise ValueError("Доступны 15m, 1h, 4h, 1d; для акций: 1h, 1d")
    return timedelta(seconds=TIMEFRAMES[timeframe])


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper().replace("/", "")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", symbol):
        raise ValueError("Некорректный тикер. Примеры: BTCUSDT, ETHUSDT, AAPL, MSFT")
    return symbol


def is_crypto(symbol: str) -> bool:
    return symbol.endswith(("USDT", "USDC"))


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Время свечи должно содержать часовой пояс")
        if self.closed_at is not None:
            if self.closed_at.tzinfo is None or self.closed_at <= self.timestamp:
                raise ValueError("Некорректное время закрытия свечи")
        if any(not isfinite(v) or v <= 0 for v in (self.open, self.high, self.low, self.close)):
            raise ValueError("Цены должны быть положительными конечными числами")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("Объём должен быть неотрицательным конечным числом")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("Некорректные максимум и минимум свечи")

    def end(self, timeframe: str) -> datetime:
        return self.closed_at or self.timestamp + timeframe_delta(timeframe)


class MarketDataProvider(Protocol):
    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]: ...


class InMemoryMarketDataProvider:
    def __init__(self, candles: dict[tuple[str, str], Sequence[Candle]]) -> None:
        self._candles = {(s.upper(), t): list(v) for (s, t), v in candles.items()}

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        try:
            return self._candles[(normalize_symbol(symbol), timeframe)][-limit:]
        except KeyError as exc:
            raise MarketDataError(f"Нет свечей для {symbol} ({timeframe})") from exc


class BinanceMarketDataProvider:
    """Только завершённые свечи; биржевые ключи не нужны."""

    BASE_URL = "https://data-api.binance.vision/api/v3/klines"

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        import requests

        symbol = normalize_symbol(symbol)
        timeframe_delta(timeframe)
        if not 1 <= limit <= 999:
            raise ValueError("Количество свечей должно быть от 1 до 999")
        try:
            response = requests.get(
                self.BASE_URL,
                params={"symbol": symbol, "interval": timeframe, "limit": limit + 1},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Неожиданный ответ")
            now = datetime.now(timezone.utc)
            candles = []
            for row in payload:
                end = datetime.fromtimestamp((row[6] + 1) / 1000, timezone.utc)
                if end > now:
                    continue
                candles.append(
                    Candle(
                        datetime.fromtimestamp(row[0] / 1000, timezone.utc),
                        *(float(row[i]) for i in range(1, 6)),
                        closed_at=end,
                    )
                )
            if not candles:
                raise ValueError("Нет завершённых свечей")
            return candles[-limit:]
        except (requests.RequestException, ValueError, TypeError, IndexError) as exc:
            raise MarketDataError(
                "Не удалось получить свечи Binance. Проверьте тикер, интернет и доступность сервиса."
            ) from exc


class YahooFinanceMarketDataProvider:
    """Акции в USD; неофициальный источник Yahoo через yfinance."""

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        import pandas as pd
        import yfinance as yf

        symbol = normalize_symbol(symbol)
        if timeframe not in {"1h", "1d"}:
            raise ValueError("Для акций доступны интервалы 1h и 1d")
        if not 1 <= limit <= 999:
            raise ValueError("Количество свечей должно быть от 1 до 999")
        try:
            ticker = yf.Ticker(symbol)
            frame = ticker.history(
                period="2y",
                interval=timeframe,
                auto_adjust=False,
                actions=False,
                timeout=self.timeout,
                raise_errors=True,
            )
            if ticker.get_history_metadata().get("currency") != "USD":
                raise MarketDataError("Поддерживаются акции с котировками в USD")
            now = datetime.now(timezone.utc)
            candles = []
            for index, row in frame.iterrows():
                start = index.to_pydatetime().astimezone(timezone.utc)
                end = (
                    (index + pd.DateOffset(days=1)).to_pydatetime().astimezone(timezone.utc)
                    if timeframe == "1d"
                    else start + timedelta(hours=1)
                )
                if end > now:
                    continue
                values = [float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume")]
                if all(isfinite(v) for v in values):
                    candles.append(Candle(start, *values, closed_at=end))
            if not candles:
                raise MarketDataError("Нет завершённых свечей для выбранной акции")
            return candles[-limit:]
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(
                "Не удалось получить данные Yahoo Finance. Проверьте тикер и повторите позже."
            ) from exc


class RoutedMarketDataProvider:
    def __init__(self, crypto: MarketDataProvider, stocks: MarketDataProvider) -> None:
        self.crypto, self.stocks = crypto, stocks

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        symbol = normalize_symbol(symbol)
        return (self.crypto if is_crypto(symbol) else self.stocks).get_candles(
            symbol, timeframe, limit
        )
