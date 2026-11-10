import asyncio
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock,patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient,TestServer
from aiogram.exceptions import TelegramForbiddenError,TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Message,Chat
from datetime import datetime,timezone

from konspektum.store import Store,Conflict
from konspektum.web import create_app
from konspektum.delivery import send_reply,send_status
from tests.helpers import config,signed


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg=replace(config(self.temp.name),bot_username='test_shop_bot')
        self.store=Store(self.cfg)
        await self.store.initialize()
        self.user={'id':200,'first_name':'Тест','username':'test_user'}
        self.cart=[{'id':1,'quantity':3,'price':4500}]
        self.bot=AsyncMock()
        self.bot.send_message.return_value=Message(message_id=44,date=datetime.now(timezone.utc),chat=Chat(id=200,type='private'))

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def order(self):
        return (await self.store.create_order(self.user,self.cart,'Не спешу','','request_12345'))[0]

    async def test_guest_draft_redeems_once_and_is_bound_to_telegram_user(self):
        token=await self.store.create_draft(self.cart,'За 2–3 дня','Физика')
        results=await asyncio.gather(*(self.store.redeem_draft(token,self.user) for _ in range(5)))
        self.assertEqual(sum(fresh for _,fresh in results),1)
        order=results[0][0]
        self.assertEqual(order['deadline'],'За 2–3 дня')
        self.assertEqual(order['total'],13500)
        with self.assertRaises(ValueError):
            await self.store.redeem_draft(token,{'id':201})

    async def test_expired_draft_and_price_change_do_not_create_order(self):
        token=await self.store.create_draft(self.cart,'Не спешу','')
        svc=(await self.store.services())[0]
        await self.store.save_service(svc|{'price':9000},svc['id'])
        with self.assertRaises(Conflict):
            await self.store.redeem_draft(token,self.user)
        await self.store.run(lambda db:db.execute('UPDATE drafts SET expires_at=0'))
        with self.assertRaises(ValueError):
            await self.store.redeem_draft(token,self.user)
        self.assertEqual(await self.store.orders(),[])

    async def test_login_verifier_is_separate_from_link_and_session_expires(self):
        token,browser,code=await self.store.login_start()
        self.assertEqual(await self.store.login_challenge(token),code)
        self.assertIsNone(await self.store.login_finish(browser))
        await self.store.login_approve(token,self.user)
        with self.assertRaises(ValueError):
            await self.store.login_finish(token)
        session=await self.store.login_finish(browser)
        identity=await self.store.session(session)
        self.assertEqual(identity['user']['id'],200)
        self.assertNotEqual(identity['csrf'],session)
        with self.assertRaises(ValueError):
            await self.store.login_finish(browser)
        await self.store.run(lambda db:db.execute('UPDATE sessions SET expires_at=0'))
        self.assertIsNone(await self.store.session(session))

    async def test_status_notifications_and_no_duplicate_for_same_status(self):
        order=await self.order()
        eid=await self.store.set_status(order['id'],'in_progress',100)
        self.assertTrue(await send_status(self.bot,self.store,eid))
        self.assertIn('В работе',self.bot.send_message.call_args.args[1])
        self.assertIsNone(await self.store.set_status(order['id'],'in_progress',100))
        self.assertFalse(await send_status(self.bot,self.store,eid))
        self.assertEqual(self.bot.send_message.await_count,1)

    async def test_status_network_error_is_queued_and_old_status_superseded(self):
        order=await self.order()
        eid=await self.store.set_status(order['id'],'discussing',100)
        self.bot.send_message.side_effect=TelegramNetworkError(SendMessage(chat_id=200,text='x'),'offline')
        self.assertFalse(await send_status(self.bot,self.store,eid))
        row=await self.store.run(lambda db:dict(db.execute('SELECT * FROM status_events WHERE id=?',(eid,)).fetchone()))
        self.assertEqual(row['state'],'pending')
        await self.store.set_status(order['id'],'done',100)
        row=await self.store.run(lambda db:dict(db.execute('SELECT * FROM status_events WHERE id=?',(eid,)).fetchone()))
        self.assertEqual(row['state'],'superseded')

    async def test_reply_is_sent_to_order_owner_and_idempotent(self):
        order=await self.order()
        result=await send_reply(self.bot,self.store,order['id'],100,'<b>Готово</b>','message_request_123')
        self.assertEqual(result['state'],'sent')
        self.assertEqual(self.bot.send_message.call_args.args[0],200)
        self.assertIn('&lt;b&gt;',self.bot.send_message.call_args.args[1])
        await send_reply(self.bot,self.store,order['id'],100,'<b>Готово</b>','message_request_123')
        self.assertEqual(self.bot.send_message.await_count,1)
        with self.assertRaises(Conflict):
            await send_reply(self.bot,self.store,order['id'],100,'Другой текст','message_request_123')

    async def test_failed_reply_and_incoming_access_control(self):
        order=await self.order()
        self.bot.send_message.side_effect=TelegramForbiddenError(SendMessage(chat_id=200,text='x'),'blocked')
        result=await send_reply(self.bot,self.store,order['id'],100,'Здравствуйте','message_request_123')
        self.assertEqual(result['state'],'failed')
        with self.assertRaises(ValueError):
            await self.store.incoming(order['id'],201,'Чужое сообщение','req_1')
        self.assertTrue(await self.store.incoming(order['id'],200,'Спасибо','req_1'))
        self.assertFalse(await self.store.incoming(order['id'],200,'Спасибо','req_1'))


class BrowserAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg=replace(config(self.temp.name),bot_username='test_shop_bot')
        self.store=Store(self.cfg)
        await self.store.initialize()
        self.client=TestClient(TestServer(create_app(self.cfg,self.store)),cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_cookie_login_and_csrf_admin_access(self):
        response=await self.client.post('/api/auth/start',json={})
        data=await response.json()
        self.assertIn('HttpOnly',response.headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',response.headers['Set-Cookie'])
        token=data['login_url'].split('login_')[1]
        self.assertFalse((await (await self.client.get(data['poll_url'])).json())['authenticated'])
        await self.store.login_approve(token,{'id':100,'first_name':'Админ'})
        self.assertTrue((await (await self.client.get(data['poll_url'])).json())['authenticated'])
        self.assertTrue((await (await self.client.get(data['poll_url'])).json())['authenticated'])
        bootstrap=await (await self.client.get('/api/bootstrap')).json()
        self.assertTrue(bootstrap['is_admin'])
        self.assertEqual((await self.client.get('/api/admin/data')).status,200)
        self.assertEqual((await self.client.patch('/api/admin/settings',json={'welcome':'Новое приветствие'})).status,403)
        headers={'X-CSRF-Token':bootstrap['csrf']}
        self.assertEqual((await self.client.patch('/api/admin/settings',headers=headers,json={'welcome':'Новое приветствие'})).status,200)
        self.assertEqual((await self.store.settings())['welcome'],'Новое приветствие')
        await self.client.post('/api/auth/logout',headers=headers,json={})
        self.assertEqual((await self.client.get('/api/admin/data')).status,401)

    async def test_login_tabs_have_separate_challenges(self):
        first=await (await self.client.post('/api/auth/start',json={})).json()
        second=await (await self.client.post('/api/auth/start',json={})).json()
        token=first['login_url'].split('login_')[1]
        await self.store.login_approve(token,{'id':100,'first_name':'Админ'})
        self.assertFalse((await (await self.client.get(second['poll_url'])).json())['authenticated'])
        self.assertTrue((await (await self.client.get(first['poll_url'])).json())['authenticated'])
        bootstrap=await (await self.client.get('/api/bootstrap')).json()
        self.assertEqual(bootstrap['user']['id'],100)

    async def test_login_survives_store_recreation_and_rejects_wrong_browser(self):
        data=await (await self.client.post('/api/auth/start',json={})).json()
        token=data['login_url'].split('login_')[1]
        restarted=Store(self.cfg)
        self.assertEqual(await restarted.login_challenge(token),data['code'])
        await restarted.login_approve(token,{'id':100})
        async with TestClient(TestServer(create_app(self.cfg,restarted)),cookie_jar=CookieJar(unsafe=True)) as stranger:
            self.assertEqual((await stranger.get(data['poll_url'])).status,400)
        self.assertTrue((await (await self.client.get(data['poll_url'])).json())['authenticated'])

    async def test_cross_origin_mutations_and_guest_draft(self):
        payload={'cart':[{'id':1,'quantity':2,'price':4500}],'deadline':'Нужно срочно','note':'Химия'}
        self.assertEqual((await self.client.post('/api/drafts',json=payload,headers={'Origin':'https://evil.invalid'})).status,403)
        response=await self.client.post('/api/drafts',json=payload)
        self.assertEqual(response.status,201)
        token=(await response.json())['bot_url'].split('order_')[1]
        order,_=await self.store.redeem_draft(token,{'id':200})
        self.assertEqual(order['deadline'],'Нужно срочно')
        self.assertEqual(order['total'],9000)

    async def test_customer_cannot_read_or_reply_to_other_order(self):
        order,_=await self.store.create_order({'id':201},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','request_1234')
        headers={'Authorization':'tma '+signed(200)}
        self.assertEqual((await self.client.get(f'/api/orders/{order["id"]}/messages',headers=headers)).status,404)
        self.assertEqual((await self.client.post(f'/api/admin/orders/{order["id"]}/messages',headers=headers,json={'body':'x','request_key':'req123456'})).status,403)
