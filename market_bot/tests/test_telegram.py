import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import Chat, Message, Update, User

from market_bot.charts import render_candlestick_chart
from market_bot.market_data import InMemoryMarketDataProvider
from market_bot.service import MarketBotService
from market_bot.telegram_bot import create_dispatcher
from test_runtime import bars


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(
                message_id=len(self.calls),
                date=datetime.now(timezone.utc),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text if isinstance(method, SendMessage) else None,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = RecordingSession()
        self.bot = Bot(token="123456789:" + "A" * 35, session=self.session)
        self.service = MarketBotService(InMemoryMarketDataProvider({("BTCUSDT", "1h"): bars()}))
        self.dispatcher = create_dispatcher(self.service, allowed_user_ids={7})

    async def asyncTearDown(self):
        await self.bot.session.close()

    async def feed(self, text, user_id=7, chat_type="private"):
        update = Update(
            update_id=len(self.session.calls) + 1,
            message=Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                text=text,
                chat=Chat(id=user_id, type=chat_type),
                from_user=User(id=user_id, is_bot=False, first_name="Тест"),
            ),
        )
        await self.dispatcher.feed_update(self.bot, update)

    async def test_unauthorized_and_group_never_read_ledger(self):
        with patch.object(self.service, "stats", side_effect=AssertionError("ledger leaked")):
            await self.feed("/stats", user_id=9)
            self.assertIn("Доступ ограничен", self.session.calls[-1].text)
            await self.feed("/stats", chat_type="group")
            self.assertIn("личный чат", self.session.calls[-1].text)

    async def test_id_available_before_access_and_authorized_stats_work(self):
        await self.feed("/id", user_id=9)
        self.assertIn("9", self.session.calls[-1].text)
        await self.feed("/stats")
        self.assertIn("Всего прогнозов: 0", self.session.calls[-1].text)

    async def test_analyze_sends_russian_report_and_png(self):
        # История в прошлом нужна только как fixture: разрешаем анализ без реальной биржи.
        with patch("market_bot.telegram_bot.render_candlestick_chart", return_value=b"png"):
            await self.feed("/analyze BTCUSDT 1h")
        self.assertTrue(any(isinstance(call, SendPhoto) for call in self.session.calls))
        self.assertTrue(
            any("Прогноз" in (getattr(call, "text", "") or "") for call in self.session.calls)
        )

    async def test_chart_is_actual_png(self):
        from io import BytesIO
        from PIL import Image

        chart = render_candlestick_chart(bars(), symbol="BTCUSDT", timeframe="1h")
        with Image.open(BytesIO(chart)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 800)
            self.assertGreater(image.height, 400)
