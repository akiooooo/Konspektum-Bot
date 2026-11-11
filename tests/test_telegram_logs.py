import asyncio
import logging
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from konspektum.telegram_logs import TelegramLogs
from tests.helpers import config


class TelegramLogsTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_orders_and_redacts_secret_without_html_parsing(self):
        cfg = replace(config('.'), log_chat_id='-1001111111111')
        bot, store = AsyncMock(), AsyncMock()
        store.settings.return_value = {'notify_chat_id': '-1002222222222'}
        handler = TelegramLogs(bot, store, cfg)
        for name in ('konspektum', 'konspektum.orders'):
            handler.emit(logging.LogRecord(name, logging.ERROR, '', 0,
                                          'test <text> ' + cfg.token, (), None))
        worker = asyncio.create_task(handler.run())
        try:
            await asyncio.wait_for(handler.queue.join(), 5)
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            handler.close()
        calls = bot.send_message.call_args_list
        self.assertEqual([c.args[0] for c in calls],
                         ['-1001111111111', '-1002222222222'])
        for call in calls:
            self.assertNotIn(cfg.token, call.args[1])
            self.assertIsNone(call.kwargs['parse_mode'])
