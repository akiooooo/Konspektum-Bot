import os
import unittest
from unittest.mock import patch

from konspektum.config import Config
from tests.helpers import TOKEN


class ConfigTests(unittest.TestCase):
    def read(self, **values):
        env = {'BOT_TOKEN': TOKEN, 'ADMIN_IDS':'100', 'SHOP_USERNAME':'real_shop', **values}
        with patch.dict(os.environ, env, clear=True), patch('konspektum.config.load_env'):
            return Config.from_env()

    def test_live_contact_is_required(self):
        for value in ('', 'your_shop_username', 'https://example.com'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(SHOP_USERNAME=value)

    def test_webapp_requires_root_https(self):
        for value in ('http://example.com', 'https://example.com/path', 'https://example.com/?token=x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(WEBAPP_URL=value)
        self.assertEqual(self.read(WEBAPP_URL='https://example.com/').webapp_url,'https://example.com')

    def test_demo_does_not_use_live_database(self):
        with patch.dict(os.environ, {'DB_PATH':'data/real-orders.db'}, clear=True), patch('konspektum.config.load_env'):
            self.assertEqual(Config.from_env(demo=True).db_path,'data/preview.sqlite3')
