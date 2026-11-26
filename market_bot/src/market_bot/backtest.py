from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from .analysis import TechnicalAnalyzer
from .market_data import Candle
from .models import ExitReason
from .paper_trading import PaperTradingAccount, TradingStats


@dataclass(frozen=True)
class BacktestResult:
    stats: TradingStats
    candles: int
    max_drawdown_usd: float


def run_backtest(
    symbol: str,
    timeframe: str,
    candles: Sequence[Candle],
    *,
    commission_rate: float = 0.001,
    timeout: timedelta = timedelta(hours=72),
) -> BacktestResult:
    """Walk forward: analysis of past bars, entry at next open, one position at a time."""
    analyzer = TechnicalAnalyzer()
    if len(candles) < 60:
        raise ValueError("Для исторического теста нужно минимум 60 завершённых свечей")
    account = PaperTradingAccount(commission_rate=commission_rate, default_timeout=timeout)
    active = None
    equity = peak = drawdown = 0.0
    for index in range(analyzer.MIN_CANDLES, len(candles)):
        candle = candles[index]
        if active is None:
            result = analyzer.analyze(symbol, timeframe, candles[max(0, index - 200) : index])
            if result.signal:
                active = account.open_signal(
                    result.signal, opened_at=candle.timestamp, pending=True
                )
        if active is not None and account.process_candle(active, candle):
            equity += active.pnl_usd or 0
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            active = None
    if active is not None:
        account.close_signal(
            active.signal.signal_id,
            candles[-1].close,
            reason=ExitReason.MANUAL,
            closed_at=candles[-1].end(timeframe),
        )
        equity += active.pnl_usd or 0
        drawdown = max(drawdown, peak - equity)
    return BacktestResult(account.stats(), len(candles), drawdown)
