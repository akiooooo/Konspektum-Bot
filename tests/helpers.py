import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from konspektum.config import Config

TOKEN = '123456789:' + 'a' * 35


def config(directory, demo=False):
    return Config(TOKEN, frozenset({100}), 'test_shop', '', '', '127.0.0.1', 8080, str(Path(directory) / 'test.db'), demo)


def signed(user_id=200, timestamp=None, **extra):
    fields = {'auth_date': str(int(time.time()) if timestamp is None else timestamp),
              'query_id': 'test-query', 'user': json.dumps({'id': user_id, 'first_name': 'Тест', 'username': 'test_user'}, ensure_ascii=False), **extra}
    secret = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
