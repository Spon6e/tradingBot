from datetime import datetime, timezone
from tempfile import TemporaryDirectory
import unittest

from market_bot import Direction, PaperTradingAccount, Signal, SQLitePositionStore


class StorageTests(unittest.TestCase):
    def test_open_position_survives_store_reopen(self) -> None:
        signal = Signal(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry_price=100,
            target_price=110,
            stop_price=95,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        with TemporaryDirectory() as directory:
            database_path = f"{directory}/market.sqlite3"
            with SQLitePositionStore(database_path) as store:
                account = PaperTradingAccount()
                position = account.open_signal(signal, opened_at=signal.created_at)
                store.save_position(position)

            with SQLitePositionStore(database_path) as reopened:
                restored = reopened.load_positions()

        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].signal.signal_id, signal.signal_id)
        self.assertEqual(restored[0].signal.symbol, "BTCUSDT")
        self.assertTrue(restored[0].is_open)

    def test_service_restores_positions_from_store(self) -> None:
        signal = Signal("AAPL", Direction.SHORT, 100, 90, 105)

        with TemporaryDirectory() as directory:
            database_path = f"{directory}/market.sqlite3"
            with SQLitePositionStore(database_path) as store:
                account = PaperTradingAccount()
                position = account.open_signal(signal)
                store.save_position(position)
                restored = store.load_positions()
                another_account = PaperTradingAccount()
                for item in restored:
                    another_account.restore_position(item)

        self.assertEqual(len(another_account.positions()), 1)
        self.assertEqual(another_account.positions()[0].signal.symbol, "AAPL")

