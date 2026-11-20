from __future__ import annotations

import asyncio

from .i18n import format_analysis, format_position, format_stats
from .service import MarketBotService


HELP_TEXT = (
    "🤖 Бот анализа рынка\n\n"
    "Команды:\n"
    "/analyze BTCUSDT — анализ криптовалюты\n"
    "/analyze AAPL 1d — анализ акции\n"
    "/positions — открытые виртуальные позиции\n"
    "/history — закрытые прогнозы\n"
    "/stats — общая статистика\n"
    "/help — эта справка\n\n"
    "Каждый сигнал открывает бумажную позицию на $100. "
    "Реальные сделки не выполняются."
)


def _command_parts(text: str | None) -> list[str]:
    return (text or "").strip().split()


def create_dispatcher(service: MarketBotService):
    """Create aiogram 3 dispatcher without importing aiogram at package import time."""
    try:
        from aiogram import Dispatcher
        from aiogram.filters import Command, CommandStart
        from aiogram.types import Message
    except ImportError as exc:
        raise RuntimeError(
            "Для Telegram-интерфейса установите зависимость: pip install '.[telegram]'"
        ) from exc

    dispatcher = Dispatcher()

    @dispatcher.message(CommandStart())
    async def start_handler(message: Message) -> None:
        await message.answer(
            "Привет! Я анализирую рынок и веду виртуальные сделки по $100.\n\n"
            "Для списка команд отправьте /help."
        )

    @dispatcher.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @dispatcher.message(Command("analyze"))
    async def analyze_handler(message: Message) -> None:
        parts = _command_parts(message.text)
        if len(parts) not in (2, 3):
            await message.answer("Формат: /analyze BTCUSDT или /analyze AAPL 1d")
            return
        symbol = parts[1].upper()
        timeframe = parts[2] if len(parts) == 3 else "1h"
        try:
            report = service.analyze_and_track(symbol, timeframe)
        except (ValueError, RuntimeError) as exc:
            await message.answer(f"Не удалось выполнить анализ:\n{exc}")
            return
        await message.answer(format_analysis(report.result, stake_usd=service.account.stake_usd))

    @dispatcher.message(Command("positions"))
    async def positions_handler(message: Message) -> None:
        positions = service.open_positions()
        if not positions:
            await message.answer("Открытых виртуальных позиций нет.")
            return
        await message.answer("\n\n".join(format_position(position) for position in positions))

    @dispatcher.message(Command("history"))
    async def history_handler(message: Message) -> None:
        positions = service.history()
        if not positions:
            await message.answer("Закрытых прогнозов пока нет.")
            return
        await message.answer("\n\n".join(format_position(position) for position in positions[-20:]))

    @dispatcher.message(Command("stats"))
    async def stats_handler(message: Message) -> None:
        await message.answer(format_stats(service.stats()))

    return dispatcher


async def _refresh_loop(service: MarketBotService, interval_seconds: int) -> None:
    while True:
        try:
            await asyncio.to_thread(service.refresh_open_positions)
        except Exception:
            # Ошибка обновления не должна останавливать Telegram-бота.
            pass
        await asyncio.sleep(interval_seconds)


async def run_bot(
    token: str,
    service: MarketBotService,
    *,
    refresh_interval_seconds: int = 60,
) -> None:
    try:
        from aiogram import Bot
        from aiogram.types import BotCommand
    except ImportError as exc:
        raise RuntimeError(
            "Для Telegram-интерфейса установите зависимость: pip install '.[telegram]'"
        ) from exc

    bot = Bot(token=token)
    await bot.set_my_commands(
        [
            BotCommand(command="analyze", description="анализ тикера"),
            BotCommand(command="positions", description="открытые позиции"),
            BotCommand(command="history", description="история прогнозов"),
            BotCommand(command="stats", description="статистика"),
            BotCommand(command="help", description="справка"),
        ]
    )
    dispatcher = create_dispatcher(service)
    refresh_task = asyncio.create_task(_refresh_loop(service, refresh_interval_seconds))
    try:
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        refresh_task.cancel()
        await asyncio.gather(refresh_task, return_exceptions=True)
        await bot.session.close()
