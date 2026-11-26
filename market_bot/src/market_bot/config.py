from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from math import isfinite


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    public_access: bool = False
    database: Path = Path("data/market_bot.sqlite3")
    refresh_seconds: int = 60
    commission_rate: float = 0.001
    timeout_hours: int = 72

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "Settings":
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
            raise ValueError("Укажите настоящий TELEGRAM_BOT_TOKEN от @BotFather в .env")
        try:
            raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
            ids = frozenset(int(s.strip()) for s in raw.split(",") if s.strip())
            refresh = int(os.getenv("REFRESH_INTERVAL_SECONDS", "60"))
            commission = float(os.getenv("PAPER_COMMISSION_RATE", "0.001"))
            hours = int(os.getenv("PAPER_TIMEOUT_HOURS", "72"))
        except ValueError as exc:
            raise ValueError("Проверьте числовые настройки в .env") from exc
        public = os.getenv("ALLOW_PUBLIC_ACCESS", "false").strip().lower()
        if public not in {"true", "false"}:
            raise ValueError("ALLOW_PUBLIC_ACCESS: укажите true или false")
        if any(i <= 0 for i in ids) or not 15 <= refresh <= 3600:
            raise ValueError("ID должны быть положительными; период обновления: 15–3600 секунд")
        if not isfinite(commission) or not 0 <= commission < 0.1 or not 1 <= hours <= 168:
            raise ValueError("Комиссия: 0–0,1; срок позиции: 1–168 часов")
        return cls(
            token,
            ids,
            public == "true",
            Path(os.getenv("MARKET_BOT_DB", "data/market_bot.sqlite3")),
            refresh,
            commission,
            hours,
        )
