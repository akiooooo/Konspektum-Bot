"""Bounded asynchronous delivery of operational logs, without recursive logging."""
import asyncio
import logging
import sys

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from .security import SecretFormatter


class TelegramLogs(logging.Handler):
    def __init__(self, bot, store, config):
        super().__init__(logging.INFO)
        self.bot, self.store, self.config = bot, store, config
        self.queue = asyncio.Queue(maxsize=500)
        self.setFormatter(SecretFormatter(config.token))

    def emit(self, record):
        try:
            text = self.format(record)[:3800]
            orders = record.name.startswith('konspektum.orders')
            self.queue.put_nowait((orders, text))
        except Exception:
            # Logging must never interrupt order handling or recursively log.
            pass

    async def run(self):
        while True:
            orders, text = await self.queue.get()
            try:
                destination = ((await self.store.settings())['notify_chat_id']
                               if orders else self.config.log_chat_id)
                if destination:
                    for attempt in range(3):
                        try:
                            await self.bot.send_message(destination, text, parse_mode=None,
                                                        request_timeout=15)
                            break
                        except TelegramRetryAfter as exc:
                            if attempt == 2:
                                raise
                            await asyncio.sleep(exc.retry_after)
                        except (TelegramAPIError, TimeoutError):
                            if attempt == 2:
                                raise
                            await asyncio.sleep(2 ** attempt)
                    await asyncio.sleep(1)
            except Exception as exc:
                # Console only: no loop of failed Telegram log notifications.
                print('Telegram log delivery failed: ' + type(exc).__name__, file=sys.stderr)
            finally:
                self.queue.task_done()
