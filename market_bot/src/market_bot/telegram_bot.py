from __future__ import annotations

import asyncio
import csv
import logging
import time
from io import StringIO

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BotCommand, BufferedInputFile, Message

from .backtest import run_backtest
from .charts import render_candlestick_chart
from .i18n import format_analysis, format_backtest, format_position, format_stats
from .market_data import is_crypto, normalize_symbol, timeframe_delta
from .service import AnalysisReport, MarketBotService

logger = logging.getLogger(__name__)
COMMANDS = {
    "start": "начать",
    "help": "справка",
    "id": "мой Telegram ID",
    "analyze": "анализ тикера",
    "scan": "обзор рынков",
    "watch": "наблюдать за тикером",
    "unwatch": "убрать наблюдение",
    "watchlist": "список наблюдения",
    "positions": "открытые позиции",
    "history": "история сделок",
    "stats": "моя статистика",
    "backtest": "тест стратегии на истории",
    "export": "история в CSV",
    "status": "состояние обновлений",
}
HELP_TEXT = (
    "📊 Анализ рынка и виртуальные сделки по $100\n\n"
    "/analyze BTCUSDT 1h — криптовалюта\n"
    "/analyze AAPL 1d — акция в USD\n"
    "/scan — обзор BTCUSDT, ETHUSDT, AAPL, MSFT\n"
    "/watch BTCUSDT 1h — автоматические прогнозы\n"
    "/unwatch BTCUSDT 1h — отменить наблюдение\n"
    "/watchlist — список наблюдения\n"
    "/positions — мои открытые позиции\n"
    "/history — последние 20 результатов\n"
    "/stats — моя статистика после комиссий\n"
    "/backtest BTCUSDT 1h — тест на истории\n"
    "/export — история в CSV\n/status — проверка обновлений\n/id — ваш Telegram ID\n\n"
    "Интервалы криптовалют: 15m, 1h, 4h, 1d; акций: 1h, 1d.\n"
    "Сигналы строятся по завершённым свечам. Вход — на открытии следующей "
    "свечи после запроса, учёт — после её завершения. Срок позиции и комиссия "
    "задаются владельцем в .env. Статистика и уведомления персональные."
)


def parse_market_args(parts: list[str]) -> tuple[str, str]:
    if len(parts) not in (2, 3):
        raise ValueError("Укажите тикер и интервал. Пример: /analyze BTCUSDT 1h")
    symbol = normalize_symbol(parts[1])
    timeframe = parts[2] if len(parts) == 3 else ("1h" if is_crypto(symbol) else "1d")
    timeframe_delta(timeframe)
    if not is_crypto(symbol) and timeframe not in {"1h", "1d"}:
        raise ValueError("Для акций доступны 1h и 1d")
    return symbol, timeframe


async def send_chunks(message: Message, text: str) -> None:
    for start in range(0, len(text), 3500):
        await message.answer(text[start : start + 3500])


async def send_report(bot: Bot, chat_id: int, report: AnalysisReport, stake: float) -> None:
    text = format_analysis(report.result, stake_usd=stake)
    if report.created:
        text += "\n\nВиртуальная заявка создана. Вход ожидается на следующей свече."
    elif report.position:
        text += "\n\nЭтот прогноз или позиция уже учтены. Повторная сделка не создаётся."
    elif report.result.signal:
        text += "\n\nНовая сделка не создана: данные устарели."
    await bot.send_message(chat_id, text)
    try:
        chart = await asyncio.to_thread(
            render_candlestick_chart,
            report.candles,
            symbol=report.result.symbol,
            timeframe=report.result.timeframe,
            signal=report.result.signal,
        )
        await bot.send_photo(chat_id, BufferedInputFile(chart, filename="analysis.png"))
    except (ValueError, RuntimeError):
        logger.warning("Не удалось построить график для %s", report.result.symbol)
        await bot.send_message(chat_id, "График временно недоступен; прогноз сохранён.")


def create_dispatcher(
    service: MarketBotService,
    *,
    allowed_user_ids: set[int] | None = None,
    public_access: bool = False,
) -> Dispatcher:
    dispatcher = Dispatcher()
    last_request: dict[int, float] = {}

    @dispatcher.message()
    async def handle(message: Message) -> None:
        if message.chat.type != "private" or message.from_user is None:
            await message.answer("Используйте личный чат с ботом.")
            return
        parts = (message.text or "").strip().split()
        command = parts[0].split("@", 1)[0].lstrip("/").lower() if parts else ""
        chat_id = message.chat.id
        if command == "id":
            await message.answer(f"Ваш Telegram ID: {message.from_user.id}")
            return
        if not public_access and message.from_user.id not in (allowed_user_ids or set()):
            await message.answer(
                f"Доступ ограничен. Ваш Telegram ID: {message.from_user.id}\n"
                "Владелец должен добавить его в TELEGRAM_ALLOWED_USER_IDS и перезапустить бота."
            )
            return
        try:
            if command in {"start", "help"}:
                await send_chunks(message, HELP_TEXT)
            elif command in {"analyze", "backtest", "watch", "unwatch", "scan"}:
                now = time.monotonic()
                if now - last_request.get(chat_id, -100) < 10:
                    await message.answer("Подождите 10 секунд между запросами анализа.")
                    return
                last_request[chat_id] = now
                if command == "scan":
                    await message.answer("Проверяю BTCUSDT, ETHUSDT, AAPL и MSFT…")
                    for symbol, timeframe in [
                        ("BTCUSDT", "1h"),
                        ("ETHUSDT", "1h"),
                        ("AAPL", "1d"),
                        ("MSFT", "1d"),
                    ]:
                        try:
                            report = await asyncio.to_thread(
                                service.analyze_and_track, symbol, timeframe, chat_id=chat_id
                            )
                            await send_report(
                                message.bot, chat_id, report, service.account.stake_usd
                            )
                        except (RuntimeError, ValueError) as exc:
                            await message.answer(f"{symbol}: {exc}")
                    return
                symbol, timeframe = parse_market_args(parts)
                if command == "watch":
                    await asyncio.to_thread(service.watch, chat_id, symbol, timeframe)
                    await message.answer(
                        f"Наблюдение включено: {symbol} ({timeframe}). Проверка каждые 5 минут."
                    )
                elif command == "unwatch":
                    await asyncio.to_thread(service.unwatch, chat_id, symbol, timeframe)
                    await message.answer(
                        f"Наблюдение отключено: {symbol} ({timeframe}). Открытые позиции продолжают учитываться."
                    )
                elif command == "backtest":
                    await message.answer("Проверяю стратегию на доступной истории…")
                    candles = await asyncio.to_thread(
                        service.data_provider.get_candles, symbol, timeframe, 999
                    )
                    result = await asyncio.to_thread(
                        run_backtest,
                        symbol,
                        timeframe,
                        candles,
                        commission_rate=service.account.commission_rate,
                        timeout=service.account.default_timeout,
                    )
                    await message.answer(format_backtest(result))
                else:
                    await message.answer(f"Анализирую {symbol} ({timeframe})…")
                    report = await asyncio.to_thread(
                        service.analyze_and_track, symbol, timeframe, chat_id=chat_id
                    )
                    await send_report(message.bot, chat_id, report, service.account.stake_usd)
            elif command in {"positions", "history"}:
                positions = (
                    await asyncio.to_thread(service.open_positions, chat_id=chat_id)
                    if command == "positions"
                    else (await asyncio.to_thread(service.history, chat_id=chat_id))[-20:]
                )
                await send_chunks(
                    message,
                    "\n\n".join(format_position(p) for p in positions) or "Позиций пока нет.",
                )
            elif command == "stats":
                await message.answer(format_stats(await asyncio.to_thread(service.stats, chat_id)))
            elif command == "watchlist":
                watches = await asyncio.to_thread(service.watches, chat_id)
                await message.answer(
                    "\n".join(f"{s} ({t})" for _, s, t in watches)
                    or "Список пуст. Пример: /watch BTCUSDT 1h"
                )
            elif command == "export":
                stream = StringIO(newline="")
                writer = csv.writer(stream)
                writer.writerow(
                    [
                        "ID",
                        "Тикер",
                        "Направление",
                        "Заявка UTC",
                        "Вход UTC",
                        "Вход",
                        "Выход",
                        "Результат USD",
                        "Доходность",
                        "Причина",
                    ]
                )
                for p in await asyncio.to_thread(service.positions, chat_id):
                    writer.writerow(
                        [
                            p.signal.signal_id,
                            p.signal.symbol,
                            p.signal.direction.value,
                            p.opened_at.isoformat(),
                            p.filled_at.isoformat() if p.filled_at else "",
                            p.signal.entry_price,
                            p.exit_price,
                            p.pnl_usd,
                            p.return_pct,
                            p.exit_reason.value if p.exit_reason else "",
                        ]
                    )
                await message.answer_document(
                    BufferedInputFile(
                        stream.getvalue().encode("utf-8-sig"), filename="paper_trades.csv"
                    )
                )
            elif command == "status":
                errors = service.refresh_errors
                own = {
                    f"{p.signal.symbol} {p.signal.timeframe}"
                    for p in service.open_positions(chat_id=chat_id)
                }
                relevant = [f"{k}: {v}" for k, v in errors.items() if k in own]
                await message.answer(
                    f"Последняя проверка (UTC): {service.last_refresh or 'ещё не было'}\n"
                    + ("\n".join(relevant) if relevant else "Ошибок обновления ваших позиций нет.")
                )
            else:
                await message.answer("Неизвестная команда. Список команд: /help")
        except (ValueError, RuntimeError) as exc:
            await message.answer(f"Не удалось выполнить запрос: {exc}")
        except Exception as exc:
            logger.error("Ошибка обработчика: %s", type(exc).__name__)
            await message.answer("Временная ошибка. Повторите запрос позже.")

    return dispatcher


async def deliver_notifications(
    service: MarketBotService, bot: Bot, allowed_user_ids: set[int] | None, public_access: bool
) -> None:
    for position in await asyncio.to_thread(service.pending_notifications):
        chat_id = position.owner_chat_id
        if not public_access and chat_id not in (allowed_user_ids or set()):
            continue
        try:
            await bot.send_message(
                chat_id, "Виртуальная сделка закрыта\n\n" + format_position(position)
            )
        except TelegramForbiddenError:
            # Пользователь заблокировал бота; прекращаем повторы для этой сделки.
            await asyncio.to_thread(service.mark_notified, position.signal.signal_id)
        except TelegramRetryAfter:
            break
        except Exception as exc:
            logger.warning("Уведомление отложено: %s", type(exc).__name__)
        else:
            await asyncio.to_thread(service.mark_notified, position.signal.signal_id)


async def _refresh_loop(
    service: MarketBotService,
    bot: Bot,
    interval_seconds: int,
    allowed_user_ids: set[int] | None,
    public_access: bool,
) -> None:
    next_scan = 0.0
    while True:
        try:
            await asyncio.to_thread(service.refresh_open_positions)
            await deliver_notifications(service, bot, allowed_user_ids, public_access)
            if time.monotonic() >= next_scan:
                for chat_id, symbol, timeframe in await asyncio.to_thread(service.watches):
                    if not public_access and chat_id not in (allowed_user_ids or set()):
                        continue
                    try:
                        report = await asyncio.to_thread(
                            service.analyze_and_track, symbol, timeframe, chat_id=chat_id
                        )
                        if report.created:
                            await send_report(bot, chat_id, report, service.account.stake_usd)
                    except Exception as exc:
                        logger.warning("Наблюдение %s отложено: %s", symbol, type(exc).__name__)
                next_scan = time.monotonic() + 300
        except Exception as exc:
            logger.error("Обновление отложено: %s", type(exc).__name__)
        await asyncio.sleep(interval_seconds)


async def run_bot(
    token: str,
    service: MarketBotService,
    *,
    refresh_interval_seconds: int = 60,
    allowed_user_ids: set[int] | None = None,
    public_access: bool = False,
) -> None:
    bot = Bot(token=token)
    refresh_task = None
    try:
        await bot.set_my_commands(
            [BotCommand(command=k, description=v) for k, v in COMMANDS.items()]
        )
        dispatcher = create_dispatcher(
            service, allowed_user_ids=allowed_user_ids, public_access=public_access
        )
        refresh_task = asyncio.create_task(
            _refresh_loop(service, bot, refresh_interval_seconds, allowed_user_ids, public_access)
        )
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        if refresh_task:
            refresh_task.cancel()
            await asyncio.gather(refresh_task, return_exceptions=True)
        await bot.session.close()
