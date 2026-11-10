from dataclasses import dataclass,field
from pathlib import Path
import os
import re
from urllib.parse import urlparse


def load_env(path: str = '.env') -> None:
    file = Path(path)
    if file.is_file():
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def username(value: str) -> str:
    if not isinstance(value,str):
        raise ValueError('Укажите Telegram username текстом.')
    value = value.strip().removeprefix('https://t.me/').lstrip('@').rstrip('/')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', value):
        raise ValueError('Укажите Telegram username: 5–32 латинских символа, цифры или _.')
    return value


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    admins: frozenset[int]
    shop_username: str
    notify_chat_id: str
    webapp_url: str
    host: str
    port: int
    db_path: str
    demo: bool = False
    bot_username: str = ''
    trusted_proxy_ips: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, demo: bool = False) -> 'Config':
        load_env()
        token = os.getenv('BOT_TOKEN', '').strip()
        try:
            admins = frozenset(int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip())
        except ValueError as exc:
            raise ValueError('ADMIN_IDS должен содержать числовые Telegram ID.') from exc
        if not demo and (not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token) or not admins or any(x <= 0 for x in admins)):
            raise ValueError('Заполните BOT_TOKEN и ADMIN_IDS в .env. Для просмотра: python -m konspektum --demo')
        webapp_url = os.getenv('WEBAPP_URL', '').strip().rstrip('/')
        parsed = urlparse(webapp_url)
        if webapp_url and (parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('WEBAPP_URL должен быть корневым HTTPS-адресом без параметров.')
        shop = os.getenv('SHOP_USERNAME', '').strip()
        if not demo and (not shop or shop == 'your_shop_username'):
            raise ValueError('Укажите реальный SHOP_USERNAME магазина в .env.')
        notify_chat = os.getenv('NOTIFY_CHAT_ID', '').strip()
        if notify_chat and not re.fullmatch(r'-?[1-9]\d{0,19}', notify_chat):
            raise ValueError('NOTIFY_CHAT_ID: числовой ID чата или пустая строка.')
        port = int(os.getenv('PORT', '8080'))
        if not 1 <= port <= 65535:
            raise ValueError('PORT должен быть от 1 до 65535.')
        return cls(token, admins, username(shop or 'konspektum_shop'),
                   notify_chat, webapp_url,
                   os.getenv('HOST', '127.0.0.1'), port,
                   'data/preview.sqlite3' if demo else os.getenv('DB_PATH', 'data/konspektum.sqlite3'), demo,
                   os.getenv('BOT_USERNAME', '').strip().lstrip('@'),
                   frozenset(x.strip() for x in os.getenv('TRUSTED_PROXY_IPS', '').split(',') if x.strip()))
