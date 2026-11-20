from datetime import datetime, timedelta, timezone
import unittest

from market_bot import Direction, TechnicalAnalyzer
from market_bot.market_data import Candle, InMemoryMarketDataProvider
from market_bot.service import MarketBotService


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_candles(start_price: float, step: float) -> list[Candle]:
    candles = []
    for index in range(80):
        close = start_price + (index * step)
        open_price = close - 0.2 if step > 0 else close + 0.2
        candles.append(
            Candle(
                timestamp=BASE_TIME + timedelta(hours=index),
                open=open_price,
                high=max(open_price, close) + 1,
                low=min(open_price, close) - 1,
                close=close,
                volume=100 + index,
            )
        )
    return candles


class AnalysisTests(unittest.TestCase):
    def test_rising_market_creates_long_signal(self) -> None:
        result = TechnicalAnalyzer().analyze("BTCUSDT", "1h", make_candles(100, 1))

        self.assertIs(result.direction, Direction.LONG)
        self.assertIsNotNone(result.signal)
        self.assertGreater(result.target_price, result.entry_price)
        self.assertLess(result.stop_price, result.entry_price)

    def test_falling_market_creates_short_signal(self) -> None:
        result = TechnicalAnalyzer().analyze("AAPL", "1d", make_candles(200, -1))

        self.assertIs(result.direction, Direction.SHORT)
        self.assertIsNotNone(result.signal)
        self.assertLess(result.target_price, result.entry_price)
        self.assertGreater(result.stop_price, result.entry_price)

    def test_service_opens_virtual_position_for_signal(self) -> None:
        candles = make_candles(100, 1)
        provider = InMemoryMarketDataProvider({("BTCUSDT", "1h"): candles})
        service = MarketBotService(provider)

        report = service.analyze_and_track("BTCUSDT", "1h")

        self.assertIsNotNone(report.position)
        self.assertEqual(report.position.stake_usd, 100)
        self.assertEqual(len(service.open_positions()), 1)

