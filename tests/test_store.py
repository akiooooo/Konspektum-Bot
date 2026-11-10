import asyncio
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from konspektum.store import Conflict, Store, contact_url
from tests.helpers import config


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(config(self.temp.name))
        await self.store.initialize()
        self.user = {'id': 200, 'first_name': 'Аня', 'username': 'anya_test'}
        self.svc = (await self.store.services())[0]
        self.cart = [{'id': self.svc['id'], 'quantity': 3, 'price': self.svc['price']}]

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def create(self, **kwargs):
        values = dict(user=self.user, cart=self.cart, deadline='Не спешу', note='Математика', request_key='request_12345') | kwargs
        return await self.store.create_order(**values)

    async def test_order_total_snapshot_and_link(self):
        order, created = await self.create()
        self.assertTrue(created)
        self.assertEqual(order['total'], 13500)
        self.assertNotIn('request_key', order)
        text = parse_qs(urlparse(contact_url('test_shop', order)).query)['text'][0]
        self.assertIn(order['code'], text)
        self.assertIn('135 ₽', text)
        await self.store.save_service(self.svc | {'price': 9900, 'title': 'Новая цена'}, self.svc['id'])
        self.assertEqual((await self.store.order(order['id']))['items'][0]['price'], 4500)

    async def test_concurrent_duplicate_submission(self):
        results = await asyncio.gather(*(self.create() for _ in range(6)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({order['id'] for order, _ in results}), 1)
        self.assertEqual(len(await self.store.orders()), 1)

    async def test_price_change_requires_reconfirmation(self):
        await self.store.save_service(self.svc | {'price': 5000}, self.svc['id'])
        with self.assertRaises(Conflict):
            await self.create()
        self.assertEqual(await self.store.orders(), [])

    async def test_hidden_service_cannot_be_ordered(self):
        await self.store.save_service(self.svc | {'active': False}, self.svc['id'])
        with self.assertRaises(Conflict):
            await self.create()
        self.assertNotIn(self.svc['id'], [s['id'] for s in await self.store.services()])

    async def test_bad_quantities_duplicates_and_prices(self):
        for value in (0, -1, 501, 1.5, True, '3'):
            with self.subTest(quantity=value), self.assertRaises(ValueError):
                await self.create(cart=[self.cart[0] | {'quantity': value}])
        with self.assertRaises(ValueError):
            await self.create(cart=self.cart * 2)
        with self.assertRaises(Conflict):
            await self.create(cart=[self.cart[0] | {'price': 1}])

    async def test_orders_are_scoped_and_status_persists(self):
        order, _ = await self.create()
        self.assertEqual(await self.store.orders(201), [])
        await self.store.set_status(order['id'], 'in_progress')
        self.assertEqual((await self.store.orders(200))[0]['status'], 'in_progress')
        with self.assertRaises(ValueError):
            await self.store.set_status(order['id'], 'paid')

    async def test_hourly_order_limit(self):
        for index in range(10):
            await self.create(request_key=f'request_{index:08}')
        with self.assertRaises(ValueError):
            await self.create(request_key='request_over_limit')
        order, created = await self.create(request_key='request_00000000')
        self.assertFalse(created)

    async def test_settings_validate_and_survive_restart(self):
        await self.store.update_settings({'shop_username': '@another_shop', 'notify_chat_id': '-10012345678'})
        await self.store.initialize()
        self.assertEqual((await self.store.settings())['shop_username'], 'another_shop')
        for change in ({'shop_username': 'https://evil.test'}, {'notify_chat_id': '0'}, {'bot_token': 'x'}):
            with self.assertRaises(ValueError):
                await self.store.update_settings(change)
