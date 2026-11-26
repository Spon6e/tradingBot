from __future__ import annotations

from io import BytesIO
from typing import Sequence
from threading import RLock

from .market_data import Candle
from .models import Signal

_CHART_LOCK = RLock()


def _render_candlestick_chart(
    candles: Sequence[Candle],
    *,
    symbol: str,
    timeframe: str,
    signal: Signal | None = None,
    max_candles: int = 80,
) -> bytes:
    """Render a compact PNG chart for Telegram."""
    if not candles or max_candles < 1:
        raise ValueError("Нельзя построить график без свечей")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError as exc:
        raise RuntimeError(
            "Для отправки графиков установите зависимости: pip install '.[telegram]'"
        ) from exc

    visible = list(candles[-max_candles:])
    figure, axis = plt.subplots(figsize=(11, 5.5), dpi=140)
    figure.patch.set_facecolor("#10151c")
    axis.set_facecolor("#10151c")
    axis.tick_params(colors="#d9e2ec")
    for spine in axis.spines.values():
        spine.set_color("#3b4754")
    axis.grid(True, color="#27313b", linewidth=0.6, alpha=0.8)

    width = 0.62
    for index, candle in enumerate(visible):
        rising = candle.close >= candle.open
        color = "#2ecc71" if rising else "#e74c3c"
        axis.vlines(index, candle.low, candle.high, color=color, linewidth=1.1)
        body_bottom = min(candle.open, candle.close)
        body_height = max(abs(candle.close - candle.open), 0.0000001)
        axis.add_patch(
            Rectangle(
                (index - width / 2, body_bottom),
                width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
            )
        )

    if signal is not None:
        axis.axhline(
            signal.entry_price, color="#f1c40f", linestyle="--", linewidth=1, label="Опорная цена"
        )
        axis.axhline(signal.target_price, color="#2ecc71", linestyle=":", linewidth=1, label="Цель")
        axis.axhline(signal.stop_price, color="#e74c3c", linestyle=":", linewidth=1, label="Стоп")
        legend = axis.legend(loc="upper left", facecolor="#17202a", edgecolor="#3b4754")
        for text in legend.get_texts():
            text.set_color("#d9e2ec")

    axis.set_title(f"{symbol} · {timeframe}", color="#f5f7fa", loc="left", pad=12)
    axis.set_xlabel("Последние свечи", color="#9fb0c0")
    axis.set_ylabel("Цена", color="#9fb0c0")
    ticks = list(range(0, len(visible), max(1, len(visible) // 6)))
    axis.set_xticks(
        ticks, [visible[i].timestamp.strftime("%d.%m %H:%M") for i in ticks], rotation=15
    )
    axis.set_xlabel("Время свечи (UTC)", color="#9fb0c0")
    axis.xaxis.label.set_color("#9fb0c0")
    axis.yaxis.label.set_color("#9fb0c0")
    figure.tight_layout()

    output = BytesIO()
    figure.savefig(output, format="png", bbox_inches="tight")
    plt.close(figure)
    return output.getvalue()


def render_candlestick_chart(candles: Sequence[Candle], **kwargs) -> bytes:
    with _CHART_LOCK:
        return _render_candlestick_chart(candles, **kwargs)
