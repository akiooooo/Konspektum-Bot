import hashlib
import hmac
import json
import time
import logging
import re
from collections import defaultdict, deque
from urllib.parse import parse_qsl


class SecretFormatter(logging.Formatter):
    def __init__(self,token):
        super().__init__('%(asctime)s %(levelname)s %(message)s')
        self.token=token

    def format(self,record):
        text=super().format(record)
        if self.token:
            text=text.replace(self.token,'[REDACTED]')
        return re.sub(r'bot\d+:[A-Za-z0-9_-]+','bot[REDACTED]',text)


def authenticate(init_data: str, token: str, now: float | None = None) -> dict:
    if not init_data or len(init_data) > 16384:
        raise ValueError('Откройте магазин через кнопку в Telegram-боте.')
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    data = dict(pairs)
    if len(pairs) != len(data):
        raise ValueError('Некорректная подпись Telegram.')
    supplied = data.pop('hash', '')
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    check = '\n'.join(f'{key}={value}' for key, value in sorted(data.items()))
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise ValueError('Некорректная подпись Telegram.')
    timestamp = int(data.get('auth_date', '0'))
    age = (time.time() if now is None else now) - timestamp
    if age < -30 or age > 3600:
        raise ValueError('Сессия истекла. Заново откройте магазин из бота.')
    user = json.loads(data.get('user', '{}'))
    if not isinstance(user, dict) or type(user.get('id')) is not int or user['id'] <= 0:
        raise ValueError('Не удалось подтвердить пользователя Telegram.')
    return user


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.last_cleanup = 0.0

    def allow(self, key: str, limit: int, window: float = 60) -> bool:
        now = time.monotonic()
        if now - self.last_cleanup > 60:
            self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v and now - v[-1] < 3600})
            self.last_cleanup = now
        if key not in self.hits and len(self.hits)>=20000:
            return False
        hits = self.hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True
