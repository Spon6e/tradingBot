"""Core package for the market analysis bot."""

from .models import Direction, ExitReason, Position, Signal
from .paper_trading import PaperTradingAccount
from .i18n import format_analysis, format_position, format_signal, format_stats
from .analysis import AnalysisResult, TechnicalAnalyzer
from .market_data import Candle, MarketDataError
from .service import AnalysisReport, MarketBotService
from .storage import SQLitePositionStore

__all__ = [
    "Direction",
    "ExitReason",
    "PaperTradingAccount",
    "Position",
    "Signal",
    "AnalysisReport",
    "AnalysisResult",
    "Candle",
    "MarketBotService",
    "MarketDataError",
    "SQLitePositionStore",
    "TechnicalAnalyzer",
    "format_analysis",
    "format_position",
    "format_signal",
    "format_stats",
]
