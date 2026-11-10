import asyncio
import unittest
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from aiogram.methods import GetUpdates

from konspektum.polling import ConflictGuard


class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_instance_stops_instead_of_retrying_forever(self):
        dispatcher=AsyncMock()
        guard=ConflictGuard(dispatcher)
        request=AsyncMock(side_effect=TelegramConflictError(method=GetUpdates(),message='conflict'))
        for _ in range(2):
            with self.assertRaises(TelegramConflictError):
                await guard(request,None,GetUpdates())
        await guard.stop_task
        self.assertTrue(guard.conflict)
        dispatcher.stop_polling.assert_awaited_once()

    async def test_network_failure_does_not_stop_polling(self):
        dispatcher=AsyncMock()
        guard=ConflictGuard(dispatcher)
        request=AsyncMock(side_effect=TelegramNetworkError(method=GetUpdates(),message='network'))
        with self.assertRaises(TelegramNetworkError):
            await guard(request,None,GetUpdates())
        self.assertFalse(guard.conflict)
        dispatcher.stop_polling.assert_not_called()
