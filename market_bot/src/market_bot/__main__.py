from __future__ import annotations

import asyncio
import os

from pathlib import Path

from .market_data import BinanceMarketDataProvider, RoutedMarketDataProvider, YahooFinanceMarketDataProvider
from .service import MarketBotService
from .storage import SQLitePositionStore
from .telegram_bot import run_bot


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не найден TELEGRAM_BOT_TOKEN. Создайте бота через @BotFather "
            "и задайте переменную окружения."
        )
    provider = RoutedMarketDataProvider(
        crypto=BinanceMarketDataProvider(),
        stocks=YahooFinanceMarketDataProvider(),
    )
    database_path = Path(os.getenv("MARKET_BOT_DB", "market_bot.sqlite3"))
    store = SQLitePositionStore(database_path)
    try:
        asyncio.run(run_bot(token, MarketBotService(provider, store=store)))
    finally:
        store.close()


if __name__ == "__main__":
    main()
