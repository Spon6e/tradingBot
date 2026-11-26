from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .market_data import Candle
from .models import Direction, Signal


@dataclass(frozen=True)
class AnalysisResult:
    symbol: str
    timeframe: str
    score: int
    direction: Direction | None
    entry_price: float
    target_price: float | None
    stop_price: float | None
    confidence: float | None
    reasons: tuple[str, ...]
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    signal: Signal | None
    analyzed_at: datetime


def _ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Нужно минимум {period} значений для EMA")
    current = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def _rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError(f"Нужно больше {period} значений для RSI")
    changes = [current - previous for previous, current in zip(values, values[1:])]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) <= period:
        raise ValueError(f"Нужно больше {period} свечей для ATR")
    ranges = []
    for previous, current in zip(candles, candles[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges[-period:]) / period


class TechnicalAnalyzer:
    """Простой объяснимый анализ по EMA, RSI и ATR.

    Это эвристический сигнал для paper trading, а не обещание результата.
    """

    MIN_CANDLES = 50

    def analyze(self, symbol: str, timeframe: str, candles: Sequence[Candle]) -> AnalysisResult:
        if len(candles) < self.MIN_CANDLES:
            raise ValueError(f"Для анализа нужно минимум {self.MIN_CANDLES} свечей")
        if any(a.timestamp >= b.timestamp for a, b in zip(candles, candles[1:])):
            raise ValueError("Свечи должны идти по времени без повторов")
        closes = [candle.close for candle in candles]
        latest = candles[-1]
        ema_fast = _ema(closes, 20)
        ema_slow = _ema(closes, 50)
        rsi = _rsi(closes)
        atr = _atr(candles)
        score = 0
        reasons: list[str] = []

        if ema_fast > ema_slow:
            score += 1
            reasons.append("EMA 20 выше EMA 50: краткосрочный тренд положительный")
        elif ema_fast < ema_slow:
            score -= 1
            reasons.append("EMA 20 ниже EMA 50: краткосрочный тренд отрицательный")

        if latest.close > ema_fast:
            score += 1
            reasons.append("цена находится выше EMA 20")
        elif latest.close < ema_fast:
            score -= 1
            reasons.append("цена находится ниже EMA 20")

        if rsi >= 55:
            score += 1
            reasons.append(f"RSI {rsi:.1f}: импульс поддерживает рост")
        elif rsi <= 45:
            score -= 1
            reasons.append(f"RSI {rsi:.1f}: импульс поддерживает падение")
        else:
            reasons.append(f"RSI {rsi:.1f}: выраженного импульса нет")

        if latest.volume >= sum(candle.volume for candle in candles[-20:]) / 20:
            reasons.append("объём не ниже среднего за 20 свечей")
        else:
            reasons.append("объём ниже среднего за 20 свечей")

        direction: Direction | None
        if score >= 2:
            direction = Direction.LONG
        elif score <= -2:
            direction = Direction.SHORT
        else:
            direction = None

        # Эвристический балл не является вероятностью успеха.
        confidence = None
        target_price = stop_price = None
        signal = None
        if atr <= 0:
            direction = None
        if direction is Direction.LONG:
            target_price = latest.close + 2 * atr
            stop_price = max(latest.close - atr, latest.close * 0.01)
        elif direction is Direction.SHORT:
            target_price = max(latest.close - 2 * atr, latest.close * 0.01)
            stop_price = latest.close + atr

        if direction is not None:
            signal = Signal(
                symbol=symbol.upper(),
                direction=direction,
                entry_price=latest.close,
                target_price=target_price,
                stop_price=stop_price,
                timeframe=timeframe,
                confidence=confidence,
                created_at=latest.timestamp,
            )

        return AnalysisResult(
            symbol=symbol.upper(),
            timeframe=timeframe,
            score=score,
            direction=direction,
            entry_price=latest.close,
            target_price=target_price,
            stop_price=stop_price,
            confidence=confidence,
            reasons=tuple(reasons),
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            atr=atr,
            signal=signal,
            analyzed_at=latest.timestamp,
        )
