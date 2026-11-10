"""Stop this instance instead of competing with another polling consumer."""
import asyncio

from aiogram.exceptions import TelegramConflictError


class ConflictGuard:
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.conflict = False
        self.stop_task = None

    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramConflictError:
            self.conflict = True
            # stop_polling waits for the current polling request to finish.
            # Schedule it separately to avoid waiting on ourselves.
            if self.stop_task is None:
                self.stop_task = asyncio.create_task(self.dispatcher.stop_polling())
            raise
