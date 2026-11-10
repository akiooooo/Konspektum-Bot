import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.methods import SendMessage

from konspektum.notifications import deliver_notice
from konspektum.store import Store
from tests.helpers import config


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg=replace(config(self.temp.name),notify_chat_id='-1001234567890')
        self.store=Store(self.cfg)
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_private_group_receives_notification(self):
        bot=AsyncMock()
        result=await deliver_notice(bot,self.store,self.cfg,'Новый заказ')
        self.assertTrue(result['ok'])
        self.assertEqual(bot.send_message.call_args.args[0],'-1001234567890')

    async def test_group_failure_is_explained_and_falls_back_to_admin(self):
        bot=AsyncMock()
        async def send(chat_id,*args,**kwargs):
            if str(chat_id).startswith('-'):
                raise TelegramBadRequest(method=SendMessage(chat_id=chat_id,text='test'),message='Bad Request: chat not found')
            return True
        bot.send_message.side_effect=send
        result=await deliver_notice(bot,self.store,self.cfg,'Новый заказ')
        self.assertFalse(result['ok'])
        self.assertTrue(result['fallback'])
        self.assertIn('Чат не найден',result['message'])
        self.assertIn('100',result['delivered_to'])

    async def test_supergroup_migration_updates_chat_id(self):
        bot=AsyncMock()
        bot.send_message.side_effect=[TelegramMigrateToChat(method=SendMessage(chat_id=-1,text='test'),message='migrated',migrate_to_chat_id=-1009876543210),True]
        result=await deliver_notice(bot,self.store,self.cfg,'Новый заказ')
        self.assertTrue(result['ok'])
        self.assertEqual((await self.store.settings())['notify_chat_id'],'-1009876543210')
        self.assertEqual(bot.send_message.await_args_list[-1].args[0],'-1009876543210')

    async def test_env_change_applies_and_panel_override_survives_same_env(self):
        await self.store.update_settings({'notify_chat_id':'-1002222222222'})
        await self.store.initialize()
        self.assertEqual((await self.store.settings())['notify_chat_id'],'-1002222222222')
        changed=Store(replace(self.cfg,notify_chat_id='-1003333333333'))
        await changed.initialize()
        self.assertEqual((await changed.settings())['notify_chat_id'],'-1003333333333')
