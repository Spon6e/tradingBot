import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from market_bot.market_data import BinanceMarketDataProvider, Candle, MarketDataError


class ProviderTests(unittest.TestCase):
    def test_incomplete_candle_excluded_and_zero_volume_preserved(self):
        now = datetime.now(timezone.utc)

        def row(start, end):
            return [
                int(start.timestamp() * 1000),
                "100",
                "102",
                "99",
                "101",
                "0",
                int(end.timestamp() * 1000) - 1,
            ]

        response = Mock()
        response.json.return_value = [
            row(now - timedelta(hours=2), now - timedelta(hours=1)),
            row(now, now + timedelta(hours=1)),
        ]
        with patch("requests.get", return_value=response):
            candles = BinanceMarketDataProvider().get_candles("BTCUSDT", "1h")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].volume, 0)

    def test_source_error_is_readable(self):
        import requests

        with patch("requests.get", side_effect=requests.Timeout):
            with self.assertRaisesRegex(MarketDataError, "Binance"):
                BinanceMarketDataProvider().get_candles("BTCUSDT", "1h")

    def test_invalid_candle_rejected(self):
        with self.assertRaises(ValueError):
            Candle(datetime.now(timezone.utc), 100, 90, 80, 100, 1)
