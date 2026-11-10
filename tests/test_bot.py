import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, Message, Update, User, InlineKeyboardMarkup

from konspektum.bot import make_bot
from konspektum.store import Store
from tests.helpers import config


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=10, date=datetime.now(timezone.utc), chat=Chat(id=int(method.chat_id), type='private'),
                           from_user=User(id=bot.id, is_bot=True, first_name='Конспектум'), text=method.text,
                           reply_markup=method.reply_markup if isinstance(method.reply_markup,InlineKeyboardMarkup) else None)
        return True

    async def stream_content(self, url, **kwargs):
        if False:
            yield b''


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = config(self.temp.name)
        self.store = Store(self.cfg)
        await self.store.initialize()
        self.bot, self.dp = make_bot(self.cfg, self.store)
        await self.bot.session.close()
        self.session = FakeSession()
        self.bot.session = self.session
        self.counter = 0

    async def asyncTearDown(self):
        await self.dp.storage.close()
        await self.bot.session.close()
        self.temp.cleanup()

    async def send(self, text=None, callback=None, uid=200,reply_to_id=None):
        self.counter += 1
        user = {'id':uid, 'is_bot':False, 'first_name':'Аня', 'username':'anya_test'}
        message = {'message_id':10, 'date':int(datetime.now().timestamp()), 'chat':{'id':uid,'type':'private'}, 'from':user, 'text':text or 'Меню'}
        if reply_to_id:
            message['reply_to_message']={'message_id':reply_to_id,'date':message['date'],'chat':message['chat'],'from':{'id':123456789,'is_bot':True,'first_name':'Конспектум'},'text':'Сообщение магазина'}
        update = {'update_id':self.counter}
        if callback:
            update['callback_query'] = {'id':str(self.counter),'from':user,'chat_instance':'test','data':callback,'message':message}
        else:
            update['message'] = message
        await self.dp.feed_update(self.bot, Update.model_validate(update))
        return [c for c in self.session.calls if isinstance(c,(SendMessage,EditMessageText))][-1]

    async def test_native_checkout_and_duplicate_click(self):
        await self.send('/start')
        await self.send(callback='add:1:5')
        await self.send(callback='checkout')
        await self.send(callback='deadline:0')
        confirm = await self.send(callback='skip')
        place = confirm.reply_markup.inline_keyboard[0][0].callback_data
        self.assertTrue(place.startswith('place:'))
        with patch('konspektum.bot.notify_order', new=AsyncMock(return_value=True)) as notify:
            result = await self.send(callback=place)
            self.assertIn('225 ₽', result.text)
            self.assertIn('https://t.me/test_shop', result.reply_markup.inline_keyboard[0][0].url)
            await self.send(callback=place)
            self.assertEqual(notify.await_count, 1)
        self.assertEqual(len(await self.store.orders(200)), 1)

    async def test_admin_denied_and_allowed(self):
        result = await self.send('/admin')
        self.assertIn('Нет доступа', result.text)
        result = await self.send('/admin', uid=100)
        self.assertIn('Управление', result.text)
        await self.send(callback='setting:shop_username',uid=100)
        await self.send('@new_shop',uid=100)
        self.assertEqual((await self.store.settings())['shop_username'],'new_shop')

    async def test_admin_service_creation_and_hiding(self):
        await self.send(callback='aa',uid=100)
        await self.send('Реферат',uid=100)
        await self.send(callback='anunit:page',uid=100)
        await self.send('65,50',uid=100)
        await self.send('По вашему тексту',uid=100)
        await self.send(callback='ancreate',uid=100)
        services = await self.store.services()
        self.assertEqual(services[-1]['price'],6550)
        await self.send(callback=f'toggle:{services[-1]["id"]}',uid=100)
        self.assertEqual(len(await self.store.services()),4)

    async def test_price_change_blocks_native_order(self):
        await self.send('/start')
        await self.send(callback='add:1:1')
        await self.send(callback='checkout')
        await self.send(callback='deadline:0')
        result=await self.send(callback='skip')
        place=result.reply_markup.inline_keyboard[0][0].callback_data
        svc=(await self.store.services())[0]
        await self.store.save_service(svc | {'price':5000},svc['id'])
        result=await self.send(callback=place)
        self.assertIn('Условия изменились',result.text)
        self.assertEqual(await self.store.orders(),[])

    async def test_other_user_order_is_hidden(self):
        order,_=await self.store.create_order({'id':201},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','test_order_1234')
        result=await self.send(callback=f'order:{order["id"]}')
        self.assertIn('Заказ не найден',result.text)

    async def test_website_link_creates_order_in_bot(self):
        token=await self.store.create_draft([{'id':1,'quantity':7,'price':4500}],'За 2–3 дня','Физика')
        with patch('konspektum.bot.notify_order',new=AsyncMock(return_value=True)) as notify:
            result=await self.send('/start order_'+token)
            self.assertIn('315 ₽',result.text)
            await self.send('/start order_'+token)
            self.assertEqual(notify.await_count,1)
        orders=await self.store.orders(200)
        self.assertEqual(len(orders),1)
        self.assertEqual(orders[0]['deadline'],'За 2–3 дня')

    async def test_website_login_needs_explicit_button(self):
        token,browser,code=await self.store.login_start()
        result=await self.send('/start login_'+token)
        self.assertIn(code,result.text)
        self.assertIsNone(await self.store.login_finish(browser))
        await self.send(callback='login:'+token)
        session=await self.store.login_finish(browser)
        self.assertEqual((await self.store.session(session))['user']['id'],200)

    async def test_admin_status_sends_notice_to_customer(self):
        order,_=await self.store.create_order({'id':200},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','status_test_123')
        await self.send(callback=f'status:{order["id"]}:in_progress',uid=100)
        notices=[call for call in self.session.calls if isinstance(call,SendMessage) and call.chat_id==200]
        self.assertEqual(len(notices),1)
        self.assertIn('В работе',notices[0].text)
        await self.send(callback=f'status:{order["id"]}:in_progress',uid=100)
        self.assertEqual(len([call for call in self.session.calls if isinstance(call,SendMessage) and call.chat_id==200]),1)

    async def test_customer_and_admin_messages_share_thread(self):
        order,_=await self.store.create_order({'id':200},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','thread_test_123')
        await self.send(callback=f'chat:{order["id"]}')
        await self.send('Нужны заголовки синим цветом.')
        await self.send(callback=f'areply:{order["id"]}',uid=100)
        await self.send('Хорошо, добавим заголовки.',uid=100)
        messages=await self.store.messages(order['id'])
        self.assertEqual([m['direction'] for m in messages],['in','out'])
        self.assertEqual(messages[-1]['state'],'sent')

    async def test_native_telegram_reply_goes_to_correct_thread(self):
        order,_=await self.store.create_order({'id':200},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','reply_test_123')
        await self.send(callback=f'areply:{order["id"]}',uid=100)
        await self.send('Какие заголовки нужны?',uid=100)
        await self.send('Синие.',reply_to_id=10)
        messages=await self.store.messages(order['id'])
        self.assertEqual([m['direction'] for m in messages],['out','in'])
        self.assertEqual(messages[-1]['body'],'Синие.')
