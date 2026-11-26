from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import timedelta

from .backtest import run_backtest
from .config import Settings
from .i18n import format_backtest
from .market_data import (
    BinanceMarketDataProvider,
    RoutedMarketDataProvider,
    YahooFinanceMarketDataProvider,
)
from .paper_trading import PaperTradingAccount
from .service import MarketBotService
from .storage import SQLitePositionStore
from .telegram_bot import run_bot


def main() -> None:
    parser = argparse.ArgumentParser(description="Русскоязычный бот анализа рынка")
    parser.add_argument("--env-file", default=".env", help="Путь к файлу настроек")
    parser.add_argument("--check", action="store_true", help="Проверить настройки без запуска")
    parser.add_argument(
        "--backtest", metavar="TICKER", help="Исторический тест без Telegram-токена"
    )
    parser.add_argument("--timeframe", default="1h")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Не допускаем вывода адресов запросов с токенами сторонними библиотеками.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    provider = RoutedMarketDataProvider(
        BinanceMarketDataProvider(), YahooFinanceMarketDataProvider()
    )
    try:
        if args.backtest:
            candles = provider.get_candles(args.backtest, args.timeframe, 999)
            print(format_backtest(run_backtest(args.backtest, args.timeframe, candles)))
            return
        settings = Settings.load(args.env_file)
        if args.check:
            print("Настройки корректны. Токен не проверялся в Telegram.")
            print(
                "Доступ: публичный"
                if settings.public_access
                else f"Приватный доступ: {len(settings.allowed_user_ids)} ID. Команда /id доступна для настройки."
            )
            return
        with SQLitePositionStore(settings.database) as store:
            service = MarketBotService(
                provider,
                store=store,
                account=PaperTradingAccount(
                    commission_rate=settings.commission_rate,
                    default_timeout=timedelta(hours=settings.timeout_hours),
                ),
            )
            asyncio.run(
                run_bot(
                    settings.token,
                    service,
                    refresh_interval_seconds=settings.refresh_seconds,
                    allowed_user_ids=set(settings.allowed_user_ids),
                    public_access=settings.public_access,
                )
            )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
