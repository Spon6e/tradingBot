"""Русские тексты и форматирование сообщений для пользовательского интерфейса."""

from __future__ import annotations

from .models import Direction, ExitReason, Position, Signal
from .paper_trading import TradingStats
from .analysis import AnalysisResult


def direction_label(direction: Direction) -> str:
    return {
        Direction.LONG: "Рост",
        Direction.SHORT: "Падение",
    }[direction]


def exit_reason_label(reason: ExitReason) -> str:
    return {
        ExitReason.TARGET: "целевая цена",
        ExitReason.STOP: "стоп-цена",
        ExitReason.TIMEOUT: "истёк срок прогноза",
        ExitReason.MANUAL: "ручное закрытие",
    }[reason]


def format_number(value: float, digits: int = 2) -> str:
    """Format a number in a Russian-friendly form: ``1 234,56``."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def format_money(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}${format_number(abs(value))}"


def format_percent(value: float) -> str:
    return f"{format_number(value * 100)}%"


def format_signal(signal: Signal, *, stake_usd: float = 100.0) -> str:
    confidence = (
        f"\nУверенность: {format_percent(signal.confidence)}"
        if signal.confidence is not None
        else ""
    )
    return (
        f"📊 Прогноз по {signal.symbol}\n"
        f"Сценарий: {direction_label(signal.direction)}\n"
        f"Таймфрейм: {signal.timeframe}\n"
        f"Цена входа: {format_number(signal.entry_price)}\n"
        f"Цель: {format_number(signal.target_price)}\n"
        f"Стоп: {format_number(signal.stop_price)}\n"
        f"Виртуальная сумма: {format_money(stake_usd)}"
        f"{confidence}"
    )


def format_analysis(result: AnalysisResult, *, stake_usd: float = 100.0) -> str:
    if result.signal is None:
        return (
            f"ℹ️ Анализ {result.symbol} ({result.timeframe})\n"
            "Сигнал: Нейтральный\n"
            "Условия для открытия виртуальной позиции не выполнены.\n\n"
            f"Оценка модели: {result.score}\n"
            f"RSI: {format_number(result.rsi, 1)}\n"
            "Причины:\n"
            + "\n".join(f"• {reason}" for reason in result.reasons)
        )
    return (
        format_signal(result.signal, stake_usd=stake_usd)
        + f"\n\nОценка модели: {result.score}\n"
        + "Причины:\n"
        + "\n".join(f"• {reason}" for reason in result.reasons)
        + "\n\n⚠️ Это вероятностный технический сценарий, а не гарантия результата."
    )


def format_position(position: Position) -> str:
    status = "Открыта" if position.is_open else "Закрыта"
    message = (
        f"📌 {position.signal.symbol} — {direction_label(position.signal.direction)}\n"
        f"Статус: {status}\n"
        f"Виртуальная сумма: {format_money(position.stake_usd)}\n"
        f"Цена входа: {format_number(position.signal.entry_price)}"
    )
    if not position.is_open:
        reason = exit_reason_label(position.exit_reason) if position.exit_reason else "не указана"
        message += (
            f"\nЦена выхода: {format_number(position.exit_price or 0)}"
            f"\nПричина: {reason}"
            f"\nРезультат: {format_money(position.pnl_usd or 0)}"
            f" ({format_percent(position.return_pct or 0)})"
        )
    return message


def format_stats(stats: TradingStats) -> str:
    profit_factor = (
        format_number(stats.profit_factor)
        if stats.profit_factor is not None
        else "н/д"
    )
    return (
        "📈 Статистика бумажной торговли\n"
        f"Всего прогнозов: {stats.total_positions}\n"
        f"Открытых: {stats.open_positions}\n"
        f"Закрытых: {stats.closed_positions}\n"
        f"Успешных: {stats.winning_positions}\n"
        f"Неуспешных: {stats.losing_positions}\n"
        f"Без результата: {stats.flat_positions}\n"
        f"Доля успешных: {format_percent(stats.win_rate)}\n"
        f"Общий результат: {format_money(stats.net_pnl_usd)}\n"
        f"Средняя доходность: {format_percent(stats.average_return_pct)}\n"
        f"Коэффициент прибыли: {profit_factor}"
    )
