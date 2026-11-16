from datetime import datetime, timedelta, timezone
import unittest

from market_bot import (
    Direction,
    ExitReason,
    PaperTradingAccount,
    Signal,
    format_position,
    format_signal,
    format_stats,
)


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PaperTradingTests(unittest.TestCase):
    def test_long_target_creates_positive_result(self) -> None:
        account = PaperTradingAccount()
        signal = Signal("BTCUSDT", Direction.LONG, 100, 110, 95)
        account.open_signal(signal, opened_at=BASE_TIME)

        closed = account.update_price(
            "BTCUSDT", 110, observed_at=BASE_TIME + timedelta(hours=1)
        )

        self.assertEqual(len(closed), 1)
        self.assertIs(closed[0].exit_reason, ExitReason.TARGET)
        self.assertAlmostEqual(closed[0].pnl_usd, 10)
        self.assertAlmostEqual(closed[0].return_pct, 0.10)


    def test_short_stop_creates_negative_result(self) -> None:
        account = PaperTradingAccount()
        signal = Signal("AAPL", Direction.SHORT, 100, 90, 105)
        account.open_signal(signal, opened_at=BASE_TIME)

        closed = account.update_price(
            "AAPL", 105, observed_at=BASE_TIME + timedelta(hours=1)
        )

        self.assertIs(closed[0].exit_reason, ExitReason.STOP)
        self.assertAlmostEqual(closed[0].pnl_usd, -5)


    def test_timeout_closes_at_observed_price(self) -> None:
        account = PaperTradingAccount(default_timeout=timedelta(hours=24))
        signal = Signal("ETHUSDT", Direction.LONG, 100, 120, 80)
        account.open_signal(signal, opened_at=BASE_TIME)

        closed = account.update_price(
            "ETHUSDT", 103, observed_at=BASE_TIME + timedelta(hours=24)
        )

        self.assertIs(closed[0].exit_reason, ExitReason.TIMEOUT)
        self.assertAlmostEqual(closed[0].pnl_usd, 3)


    def test_stats_track_open_and_closed_positions(self) -> None:
        account = PaperTradingAccount()
        winning_signal = Signal("BTCUSDT", Direction.LONG, 100, 110, 90)
        losing_signal = Signal("ETHUSDT", Direction.SHORT, 100, 90, 110)
        account.open_signal(winning_signal, opened_at=BASE_TIME)
        account.open_signal(losing_signal, opened_at=BASE_TIME)
        account.update_price("BTCUSDT", 110, observed_at=BASE_TIME + timedelta(hours=1))
        account.update_price("ETHUSDT", 110, observed_at=BASE_TIME + timedelta(hours=1))

        stats = account.stats()

        self.assertEqual(stats.total_positions, 2)
        self.assertEqual(stats.closed_positions, 2)
        self.assertEqual(stats.open_positions, 0)
        self.assertEqual(stats.winning_positions, 1)
        self.assertEqual(stats.losing_positions, 1)
        self.assertAlmostEqual(stats.win_rate, 0.5)
        self.assertAlmostEqual(stats.net_pnl_usd, 0)

    def test_interface_messages_are_in_russian(self) -> None:
        account = PaperTradingAccount()
        signal = Signal("BTCUSDT", Direction.LONG, 100, 110, 95, confidence=0.75)
        position = account.open_signal(signal, opened_at=BASE_TIME)

        self.assertIn("Сценарий: Рост", format_signal(signal))
        self.assertIn("Уверенность: 75,00%", format_signal(signal))
        self.assertIn("Статус: Открыта", format_position(position))
        self.assertIn("Статистика бумажной торговли", format_stats(account.stats()))


if __name__ == "__main__":
    unittest.main()

