import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from konspektum.store import Store
from konspektum.web import create_app
from tests.helpers import config, signed


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = config(self.temp.name)
        self.store = Store(self.cfg)
        await self.store.initialize()
        self.client = TestClient(TestServer(create_app(self.cfg, self.store)))
        await self.client.start_server()
        self.user = {'Authorization': 'tma ' + signed(200)}
        self.admin = {'Authorization': 'tma ' + signed(100)}

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_public_catalog_has_no_private_settings(self):
        response = await self.client.get('/api/bootstrap')
        data = await response.json()
        self.assertEqual(response.status, 200)
        self.assertFalse(data['is_admin'])
        self.assertNotIn('notify_chat_id', data['settings'])
        self.assertIn('nosniff', response.headers['X-Content-Type-Options'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    async def test_chunked_json_cannot_bypass_body_limit(self):
        async def chunks():
            yield b'{"note":"'
            yield b'x' * 40000
            yield b'"}'
        response = await self.client.post('/api/orders', data=chunks(),
            headers=self.user | {'Content-Type': 'application/json'})
        self.assertEqual(response.status, 413)

    async def test_auth_and_admin_permissions(self):
        self.assertEqual((await self.client.get('/api/orders')).status, 401)
        self.assertEqual((await self.client.get('/api/admin/data', headers=self.user)).status, 403)
        self.assertEqual((await self.client.get('/api/admin/data', headers=self.admin)).status, 200)
        response = await self.client.get('/api/bootstrap', headers=self.admin)
        self.assertTrue((await response.json())['is_admin'])

    async def test_order_flow_and_isolation(self):
        payload = {'cart': [{'id': 1, 'quantity': 2, 'price': 4500}], 'deadline': 'Не спешу', 'note': 'Физика', 'request_key': 'api_test_1234'}
        response = await self.client.post('/api/orders', json=payload, headers=self.user)
        self.assertEqual(response.status, 201)
        data = await response.json()
        self.assertEqual(data['order']['total'], 9000)
        self.assertTrue(data['contact_url'].endswith('?start=chat_'+str(data['order']['id'])))
        self.assertEqual((await self.client.post('/api/orders', json=payload, headers=self.user)).status, 200)
        other = await self.client.get('/api/orders', headers={'Authorization':'tma ' + signed(201)})
        self.assertEqual((await other.json())['orders'], [])
        own = await self.client.get('/api/orders', headers=self.user)
        self.assertEqual(len((await own.json())['orders']), 1)

    async def test_admin_edit_and_price_conflict(self):
        svc = (await self.store.services())[0] | {'price': 5500}
        self.assertEqual((await self.client.patch('/api/admin/services/1', json=svc, headers=self.user)).status, 403)
        self.assertEqual((await self.client.patch('/api/admin/services/1', json=svc, headers=self.admin)).status, 200)
        response = await self.client.post('/api/orders', headers=self.user, json={'cart':[{'id':1,'quantity':1,'price':4500}], 'deadline':'Не спешу','request_key':'conflict_12345'})
        self.assertEqual(response.status, 409)

    async def test_static_allowlist_and_invalid_body(self):
        self.assertEqual((await self.client.get('/assets/.env')).status, 404)
        self.assertEqual((await self.client.get('/assets/../config.py')).status, 404)
        self.assertEqual((await self.client.post('/api/orders', headers=self.user, json=[])).status, 400)
        self.assertEqual((await self.client.post('/api/orders', headers=self.user|{'Content-Type':'application/json'}, data='broken')).status, 400)

    async def test_demo_cannot_create_or_admin(self):
        client = TestClient(TestServer(create_app(config(self.temp.name, demo=True), self.store)))
        await client.start_server()
        try:
            self.assertEqual((await client.post('/api/orders', headers=self.admin, json={})).status, 401)
            self.assertEqual((await client.get('/api/admin/data', headers=self.admin)).status, 401)
        finally:
            await client.close()
