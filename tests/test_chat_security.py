import io
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from aiohttp import CookieJar, FormData
from aiohttp.test_utils import TestClient, TestServer
from aiogram.types import Chat, File, Message, PhotoSize
from datetime import datetime, timezone

from konspektum.web import create_app
from konspektum.store import Store
from tests.helpers import signed, config


class ChatSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg=replace(config(self.temp.name),bot_username='test_shop_bot')
        self.store=Store(self.cfg)
        await self.store.initialize()
        self.bot=AsyncMock()
        self.bot.send_photo.return_value=Message(message_id=77,date=datetime.now(timezone.utc),chat=Chat(id=200,type='private'),photo=[PhotoSize(file_id='saved_photo',file_unique_id='unique',width=100,height=100,file_size=100)])
        self.bot.get_file.return_value=File(file_id='saved_photo',file_unique_id='unique',file_size=100,file_path='photos/file.jpg')
        self.bot.download_file.return_value=io.BytesIO(b'photo-content')
        self.client=TestClient(TestServer(create_app(self.cfg,self.store,self.bot)),cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.order,_=await self.store.create_order({'id':200},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','chat_order_123')

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_photo_upload_download_permissions_and_duplicate(self):
        def photo(content=b'\x89PNG\r\n\x1a\nimage'):
            form=FormData()
            form.add_field('photo',content,filename='photo.png',content_type='image/png')
            form.add_field('body','Фото результата')
            form.add_field('request_key','photo_request_123')
            return form
        path=f'/api/admin/orders/{self.order["id"]}/photo'
        self.assertEqual((await self.client.post(path,data=photo())).status,401)
        self.assertEqual((await self.client.post(path,data=photo(),headers={'Authorization':'tma '+signed(200)})).status,403)
        admin={'Authorization':'tma '+signed(100)}
        self.assertEqual((await self.client.post(path,data=photo(b'not an image'),headers=admin)).status,400)
        self.assertEqual((await self.client.post(path,data=photo(),headers=admin)).status,200)
        self.assertEqual((await self.client.post(path,data=photo(),headers=admin)).status,200)
        self.bot.send_photo.assert_awaited_once()
        messages=await self.store.messages(self.order['id'])
        self.assertEqual(messages[0]['attachment']['file_id'],'saved_photo')
        attachment=f'/api/orders/{self.order["id"]}/messages/{messages[0]["id"]}/attachment'
        self.assertEqual((await self.client.get(attachment)).status,401)
        self.assertEqual((await self.client.get(attachment,headers={'Authorization':'tma '+signed(201)})).status,404)
        response=await self.client.get(attachment,headers={'Authorization':'tma '+signed(200)})
        self.assertEqual(response.status,200)
        self.assertEqual(await response.read(),b'photo-content')
        self.assertIn('attachment',response.headers['Content-Disposition'])
        other,_=await self.store.create_order({'id':201},[{'id':1,'quantity':1,'price':4500}],'Не спешу','','other_photo_order')
        mixed=f'/api/orders/{other["id"]}/messages/{messages[0]["id"]}/attachment'
        self.assertEqual((await self.client.get(mixed,headers={'Authorization':'tma '+signed(201)})).status,404)

    async def test_order_deletion_requires_admin_and_matching_confirmation(self):
        path=f'/api/admin/orders/{self.order["id"]}'
        body={'confirmation':self.order['code']}
        self.assertEqual((await self.client.delete(path,json=body,headers={'Authorization':'tma '+signed(200)})).status,403)
        admin={'Authorization':'tma '+signed(100)}
        self.assertEqual((await self.client.delete(path,json={'confirmation':'wrong'},headers=admin)).status,400)
        await self.store.incoming(self.order['id'],200,'Фото','photo_msg',{'kind':'photo','file_id':'photo','name':'Фото.jpg','size':100})
        await self.store.set_status(self.order['id'],'done',100)
        self.assertEqual((await self.client.delete(path,json=body,headers=admin)).status,200)
        self.assertEqual(await self.store.messages(self.order['id']),[])
        self.assertEqual(await self.store.pending_statuses(),[])
        with self.assertRaises(ValueError):
            await self.store.order(self.order['id'])

    async def test_global_logout_revokes_two_browsers_pending_logins_and_miniapp(self):
        async def login(client):
            data=await (await client.post('/api/auth/start',json={})).json()
            await self.store.login_approve(data['login_url'].split('login_')[1],{'id':100})
            self.assertTrue((await (await client.get(data['poll_url'])).json())['authenticated'])
        async with TestClient(TestServer(create_app(self.cfg,self.store)),cookie_jar=CookieJar(unsafe=True)) as second:
            await login(self.client)
            await login(second)
            pending,browser,_=await self.store.login_start()
            await self.store.login_approve(pending,{'id':100})
            signed_before=signed(100)
            self.assertEqual((await second.get('/api/admin/data')).status,200)
            csrf=(await (await self.client.get('/api/bootstrap')).json())['csrf']
            self.assertEqual((await self.client.post('/api/auth/logout',json={},headers={'X-CSRF-Token':csrf})).status,200)
            self.assertEqual((await second.get('/api/admin/data')).status,401)
            self.assertEqual((await self.client.get('/api/admin/data')).status,401)
            self.assertEqual((await second.get('/api/admin/data',headers={'Authorization':'tma '+signed_before})).status,401)
            with self.assertRaises(ValueError):
                await self.store.login_finish(browser)
            # A newly confirmed browser login is still allowed.
            await login(second)
            self.assertEqual((await second.get('/api/admin/data')).status,200)
