import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from market_bot import Direction, ExitReason, PaperTradingAccount, Signal, TechnicalAnalyzer
from market_bot.backtest import run_backtest
from market_bot.config import Settings
from market_bot.market_data import Candle, InMemoryMarketDataProvider
from market_bot.service import MarketBotService
from market_bot.storage import SQLitePositionStore

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(count=80, start=100, step=1):
    return [
        Candle(
            BASE + timedelta(hours=i),
            start + i * step,
            start + i * step + 1,
            start + i * step - 1,
            start + i * step + 0.1,
            100,
        )
        for i in range(count)
    ]


class EngineTests(unittest.TestCase):
    def test_intrabar_stop_wins_and_gap_uses_open(self):
        for direction, target, stop, candle, expected in [
            (Direction.LONG, 110, 95, Candle(BASE, 100, 112, 94, 101, 0), 95),
            (Direction.LONG, 110, 95, Candle(BASE, 90, 93, 89, 92, 0), 90),
            (Direction.SHORT, 90, 105, Candle(BASE, 100, 106, 89, 99, 0), 105),
            (Direction.SHORT, 90, 105, Candle(BASE, 109, 110, 108, 109, 0), 109),
        ]:
            with self.subTest(direction=direction, price=expected):
                account = PaperTradingAccount()
                p = account.open_signal(
                    Signal("BTCUSDT", direction, 100, target, stop), opened_at=BASE
                )
                self.assertTrue(account.process_candle(p, candle))
                self.assertEqual(p.exit_price, expected)
                self.assertEqual(p.exit_reason, ExitReason.STOP)

    def test_pending_uses_future_open_and_actual_exit_notional_fee(self):
        account = PaperTradingAccount(commission_rate=0.001)
        p = account.open_signal(
            Signal("BTCUSDT", Direction.LONG, 100, 110, 95), opened_at=BASE, pending=True
        )
        account.process_candle(p, Candle(BASE - timedelta(hours=1), 100, 200, 50, 120, 0))
        self.assertTrue(p.pending)
        self.assertTrue(account.process_candle(p, Candle(BASE, 200, 225, 199, 220, 0)))
        self.assertEqual(p.signal.entry_price, 200)
        self.assertEqual(p.quantity, 0.5)
        self.assertAlmostEqual(p.pnl_usd, 10 - 0.21)

    def test_timeout_precedes_later_candle_extremes(self):
        account = PaperTradingAccount(default_timeout=timedelta(hours=1))
        p = account.open_signal(Signal("AAPL", Direction.LONG, 100, 110, 95), opened_at=BASE)
        account.process_candle(p, Candle(BASE + timedelta(hours=1), 102, 115, 90, 100, 0))
        self.assertEqual(p.exit_reason, ExitReason.TIMEOUT)
        self.assertEqual(p.exit_price, 102)

    def test_flat_market_neutral_and_invalid_numbers_rejected(self):
        flat = [Candle(BASE + timedelta(hours=i), 100, 100, 100, 100, 0) for i in range(60)]
        result = TechnicalAnalyzer().analyze("BTCUSDT", "1h", flat)
        self.assertIsNone(result.signal)
        self.assertEqual(result.rsi, 50)
        with self.assertRaises(ValueError):
            Signal("BTCUSDT", Direction.LONG, float("nan"), 110, 95)

    def test_backtest_never_uses_future_signal_candles(self):
        seen = []
        original = TechnicalAnalyzer.analyze

        def spy(analyzer, symbol, timeframe, candles):
            seen.append(candles[-1].timestamp)
            return original(analyzer, symbol, timeframe, candles)

        with patch.object(TechnicalAnalyzer, "analyze", spy):
            result = run_backtest("BTCUSDT", "1h", bars())
        self.assertEqual(seen[0], BASE + timedelta(hours=49))
        self.assertGreater(result.stats.closed_positions, 0)
        self.assertEqual(result.stats.open_positions, 0)


class ServiceTests(unittest.TestCase):
    def test_restart_dedup_ownership_notification_and_watch_persistence(self):
        candles = bars()
        provider = InMemoryMarketDataProvider({("BTCUSDT", "1h"): candles})
        now = candles[-1].end("1h")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            with SQLitePositionStore(path) as store:
                service = MarketBotService(provider, store=store)
                a = service.analyze_and_track("BTCUSDT", chat_id=1, now=now)
                b = service.analyze_and_track("BTCUSDT", chat_id=1, now=now)
                service.analyze_and_track("BTCUSDT", chat_id=2, now=now)
                service.watch(1, "BTCUSDT", "1h")
                self.assertTrue(a.created)
                self.assertFalse(b.created)
                self.assertEqual(len(service.open_positions(chat_id=1)), 1)
                self.assertEqual(service.stats(2).total_positions, 1)
            provider._candles[("BTCUSDT", "1h")] = candles + [Candle(now, 180, 220, 179, 210, 200)]
            with SQLitePositionStore(path) as store:
                service = MarketBotService(provider, store=store)
                self.assertEqual(len(service.watches(1)), 1)
                service.refresh_open_positions()
                self.assertEqual(len(service.pending_notifications()), 2)
                self.assertEqual(len(service.history(chat_id=1)), 1)
                service.mark_notified(a.position.signal.signal_id)
            with SQLitePositionStore(path) as store:
                service = MarketBotService(provider, store=store)
                self.assertEqual(len(service.pending_notifications()), 1)
                service.refresh_open_positions()
                self.assertEqual(len(service.history()), 2)

    def test_provider_failure_does_not_block_other_symbols(self):
        provider = InMemoryMarketDataProvider({("ETHUSDT", "1h"): bars()})
        service = MarketBotService(provider)
        for symbol in ["BTCUSDT", "ETHUSDT"]:
            service.account.open_signal(
                Signal(symbol, Direction.LONG, 100, 110, 95), opened_at=BASE
            )
        service.refresh_open_positions()
        self.assertIn("BTCUSDT 1h", service.refresh_errors)
        self.assertEqual(len(service.history()), 1)

    def test_old_sqlite_schema_is_migrated(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE positions (
                    signal_id TEXT PRIMARY KEY, symbol TEXT, direction TEXT, entry_price REAL,
                    target_price REAL, stop_price REAL, timeframe TEXT, confidence REAL,
                    created_at TEXT, stake_usd REAL, quantity REAL, opened_at TEXT,
                    exit_price REAL, exit_reason TEXT, closed_at TEXT, pnl_usd REAL, return_pct REAL)""")
            db.close()
            with SQLitePositionStore(path) as store:
                self.assertEqual(store.load_positions(), [])
                p = PaperTradingAccount().open_signal(Signal("AAPL", Direction.LONG, 100, 110, 95))
                store.save_position(p)
                self.assertEqual(len(store.load_positions()), 1)


class ConfigTests(unittest.TestCase):
    def test_env_loading_and_environment_priority(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            # Не сохраняем тестовый токен в исходном коде одной строкой.
            token = "123456789:" + "A" * 35
            path.write_text(
                f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_ALLOWED_USER_IDS=1,2\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {"PAPER_COMMISSION_RATE": "0.002"}, clear=True):
                settings = Settings.load(path)
                self.assertEqual(settings.allowed_user_ids, frozenset({1, 2}))
                self.assertEqual(settings.commission_rate, 0.002)
                self.assertNotIn(token, repr(settings))

    def test_invalid_setting_rejected(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35, "PAPER_COMMISSION_RATE": "nan"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                Settings.load("missing.env")


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failure_is_retried_and_owner_only(self):
        from market_bot.telegram_bot import deliver_notifications

        service = MarketBotService(InMemoryMarketDataProvider({}))
        p = service.account.open_signal(
            Signal("BTCUSDT", Direction.LONG, 100, 110, 95), owner_chat_id=7
        )
        service.account.close_signal(p.signal.signal_id, 110)
        bot = AsyncMock()
        bot.send_message.side_effect = RuntimeError("temporary")
        await deliver_notifications(service, bot, {7}, False)
        self.assertFalse(p.notified)
        bot.send_message.side_effect = None
        await deliver_notifications(service, bot, {7}, False)
        self.assertTrue(p.notified)
        self.assertEqual(bot.send_message.call_args.args[0], 7)


if __name__ == "__main__":
    unittest.main()
