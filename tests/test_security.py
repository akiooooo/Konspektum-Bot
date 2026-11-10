import unittest
import logging
from urllib.parse import parse_qsl, urlencode

from konspektum.security import RateLimiter, authenticate,SecretFormatter
from konspektum.store import price
from tests.helpers import TOKEN, signed


class SecurityTests(unittest.TestCase):
    def test_valid_signature(self):
        self.assertEqual(authenticate(signed(42), TOKEN)['id'], 42)

    def test_tampered_user(self):
        fields = dict(parse_qsl(signed()))
        fields['user'] = '{"id":100}'
        with self.assertRaises(ValueError):
            authenticate(urlencode(fields), TOKEN)

    def test_expired_future_and_duplicate(self):
        for payload in (signed(timestamp=100), signed(timestamp=10000), signed(timestamp=1000) + '&auth_date=1000'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                authenticate(payload, TOKEN, now=5000)

    def test_current_and_boundary(self):
        self.assertEqual(authenticate(signed(timestamp=1000), TOKEN, now=1000)['id'], 200)
        self.assertEqual(authenticate(signed(timestamp=1000), TOKEN, now=4600)['id'], 200)
        with self.assertRaises(ValueError):
            authenticate(signed(timestamp=1000), TOKEN, now=4601)

    def test_non_object_user(self):
        with self.assertRaises(ValueError):
            authenticate(signed(user='[]'), TOKEN)

    def test_rate_limit(self):
        limiter = RateLimiter()
        self.assertTrue(limiter.allow('user:1', 2))
        self.assertTrue(limiter.allow('user:1', 2))
        self.assertFalse(limiter.allow('user:1', 2))
        self.assertTrue(limiter.allow('user:2', 2))

    def test_prices_use_exact_kopeks(self):
        self.assertEqual(price('45,99'), 4599)
        self.assertEqual(price('0.01'), 1)
        for value in ('NaN', 'Infinity', '-1', '0', '0.001', '1000001', 'abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                price(value)

    def test_logs_redact_bot_token(self):
        record=logging.LogRecord('test',logging.ERROR,'test',1,'Request to https://api.telegram.org/bot%s/getMe',(TOKEN,),None)
        output=SecretFormatter(TOKEN).format(record)
        self.assertNotIn(TOKEN,output)
        self.assertIn('[REDACTED]',output)
