import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from .config import Config, username

STATUSES = {'new': 'Новый', 'discussing': 'Обсуждаем', 'in_progress': 'В работе', 'done': 'Готов', 'cancelled': 'Отменён'}
DEADLINES = ('Не спешу', 'В течение недели', 'За 2–3 дня', 'Нужно срочно')


class Conflict(ValueError):
    pass


def money(kopeks: int) -> str:
    return f'{kopeks / 100:,.2f}'.replace(',', ' ').removesuffix('.00').replace('.', ',') + ' ₽'


def price(value: str) -> int:
    try:
        amount = Decimal(str(value).replace(',', '.').replace(' ', ''))
        if not amount.is_finite() or amount < Decimal('0.01') or amount > 1000000 or amount.as_tuple().exponent < -2:
            raise ValueError('Цена: от 0,01 до 1 000 000 ₽, не больше двух знаков после запятой.')
        return int(amount * 100)
    except InvalidOperation as exc:
        raise ValueError('Укажите цену числом в рублях.') from exc


def clean_text(value, label: str, maximum: int, minimum: int = 0) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{label}: нужен текст.')
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f'{label}: от {minimum} до {maximum} символов.')
    return value


def service_data(data: dict) -> dict:
    amount = data.get('price')
    maximum = data.get('max_quantity', 500)
    if type(amount) is not int or not 1 <= amount <= 100000000:
        raise ValueError('Некорректная цена услуги.')
    if type(maximum) is not int or not 1 <= maximum <= 5000:
        raise ValueError('Максимальное количество: от 1 до 5000.')
    if type(data.get('active', True)) is not bool:
        raise ValueError('Некорректный статус услуги.')
    return dict(title=clean_text(data.get('title'), 'Название', 80, 1),
                description=clean_text(data.get('description', ''), 'Описание', 400),
                unit=clean_text(data.get('unit', 'страница'), 'Единица', 30, 1), price=amount,
                icon=clean_text(data.get('icon', '✦'), 'Значок', 8, 1), max_quantity=maximum,
                active=data.get('active', True))


class Store:
    def __init__(self, config: Config):
        self.config = config
        Path(config.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        db = sqlite3.connect(self.config.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    async def run(self, operation):
        def execute():
            db = self.connect()
            try:
                with db:
                    return operation(db)
            finally:
                db.close()
        return await asyncio.to_thread(execute)

    async def initialize(self):
        def init(db):
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS services (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    unit TEXT NOT NULL, price INTEGER NOT NULL CHECK(price > 0), icon TEXT NOT NULL,
                    max_quantity INTEGER NOT NULL DEFAULT 500, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL,
                    username TEXT NOT NULL, name TEXT NOT NULL, items TEXT NOT NULL,
                    total INTEGER NOT NULL, deadline TEXT NOT NULL, note TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL,
                    request_key TEXT NOT NULL, UNIQUE(user_id, request_key));
                CREATE INDEX IF NOT EXISTS orders_user ON orders(user_id, id DESC);
                CREATE TABLE IF NOT EXISTS drafts (
                    token_hash TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at INTEGER NOT NULL,
                    user_id INTEGER, order_id INTEGER REFERENCES orders(id));
                CREATE INDEX IF NOT EXISTS drafts_expiry ON drafts(expires_at);
                CREATE TABLE IF NOT EXISTS login_challenges (
                    token_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL UNIQUE, code TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, approved_user TEXT, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_json TEXT NOT NULL, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                    direction TEXT NOT NULL, body TEXT NOT NULL, actor_id INTEGER NOT NULL,
                    state TEXT NOT NULL, telegram_message_id INTEGER, created_at TEXT NOT NULL,
                    request_key TEXT NOT NULL, UNIQUE(order_id, direction, request_key));
                CREATE INDEX IF NOT EXISTS messages_order ON messages(order_id, id);
                CREATE TABLE IF NOT EXISTS status_events (
                    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                    status TEXT NOT NULL, actor_id INTEGER, state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')));
            ''')
            for key, value in {'shop_username': self.config.shop_username, 'notify_chat_id': self.config.notify_chat_id,
                               'welcome': 'Ваши мысли — наш аккуратный почерк.',
                               'about': 'Пишем конспекты на машинке по вашим материалам. Объём, сроки и детали согласуем в личных сообщениях. Оплата — напрямую магазину после согласования.'}.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', (key, value))
            if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
                for item in [
                    ('Конспект', 'Аккуратно перенесём ваши материалы в тетрадь. Читаемо, ровно, с вниманием к каждой строке.', 'страница', 4500, '✎'),
                    ('Лекция', 'Превратим текст лекции в чистовую запись. Заголовки и структура уже включены.', 'страница', 5500, '≋'),
                    ('Таблицы и схемы', 'Оформим таблицы и простые схемы по вашему образцу. Сложные элементы обсудим отдельно.', 'элемент', 8000, '▦'),
                    ('Титульный лист', 'Оформим титульный лист по требованиям вашего учебного заведения.', 'лист', 10000, '↗')]:
                    db.execute('INSERT INTO services(title,description,unit,price,icon) VALUES(?,?,?,?,?)', item)
                db.execute("INSERT INTO settings VALUES('seeded','1')")
        await self.run(init)

    async def settings(self):
        return await self.run(lambda db: dict(db.execute('SELECT key,value FROM settings')))

    async def update_settings(self, data: dict):
        allowed = {'shop_username', 'notify_chat_id', 'welcome', 'about'}
        if not data or set(data) - allowed:
            raise ValueError('Неизвестная настройка.')
        result = {}
        for key, value in data.items():
            if key == 'shop_username':
                result[key] = username(value)
            elif key == 'notify_chat_id':
                value = clean_text(value, 'Чат уведомлений', 50)
                if value and not re.fullmatch(r'-?[1-9]\d{0,19}', value):
                    raise ValueError('Чат уведомлений: числовой Telegram ID или пустая строка.')
                result[key] = value
            else:
                result[key] = clean_text(value, 'Текст', 1000 if key == 'about' else 120, 1)
        await self.run(lambda db: db.executemany('UPDATE settings SET value=? WHERE key=?', [(v, k) for k, v in result.items()]))

    async def services(self, all_services=False):
        return await self.run(lambda db: [dict(row) | {'active': bool(row['active'])} for row in db.execute(
            'SELECT * FROM services' + ('' if all_services else ' WHERE active=1') + ' ORDER BY id')])

    async def save_service(self, data: dict, service_id: int | None = None):
        item = service_data(data)
        def save(db):
            if service_id is None:
                if db.execute('SELECT COUNT(*) FROM services').fetchone()[0] >= 100:
                    raise ValueError('Достигнут лимит: 100 услуг. Отредактируйте существующие.')
                return db.execute('INSERT INTO services(title,description,unit,price,icon,max_quantity,active) VALUES(?,?,?,?,?,?,?)', tuple(item.values())).lastrowid
            cursor = db.execute('UPDATE services SET title=?,description=?,unit=?,price=?,icon=?,max_quantity=?,active=? WHERE id=?', (*item.values(), service_id))
            if not cursor.rowcount:
                raise ValueError('Услуга не найдена.')
            return service_id
        return await self.run(save)

    @staticmethod
    def quote(db, cart: list) -> tuple[list, int]:
        if not isinstance(cart, list) or not 1 <= len(cart) <= 100:
            raise ValueError('Добавьте услуги в заказ (не более 100 позиций).')
        items, seen, total = [], set(), 0
        for entry in cart:
            if not isinstance(entry, dict):
                raise ValueError('Некорректная корзина.')
            service_id, quantity = entry.get('id'), entry.get('quantity')
            if type(service_id) is not int or type(quantity) is not int or service_id in seen:
                raise ValueError('Некорректная позиция корзины.')
            seen.add(service_id)
            row = db.execute('SELECT * FROM services WHERE id=? AND active=1', (service_id,)).fetchone()
            if row is None:
                raise Conflict('Услуга больше недоступна. Обновите корзину.')
            if not 1 <= quantity <= row['max_quantity']:
                raise ValueError(f'«{row["title"]}»: количество от 1 до {row["max_quantity"]}.')
            if type(entry.get('price')) is not int or entry['price'] != row['price']:
                raise Conflict('Цены изменились. Проверьте обновлённую корзину и подтвердите заказ ещё раз.')
            subtotal = quantity * row['price']
            items.append(dict(id=service_id, title=row['title'], unit=row['unit'], price=row['price'], quantity=quantity, subtotal=subtotal))
            total += subtotal
        if total > 100000000:
            raise ValueError('Заказы свыше 1 000 000 ₽ согласуйте с магазином напрямую.')
        return items, total

    async def create_order(self, user: dict, cart: list, deadline: str, note: str, request_key: str):
        if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_key):
            raise ValueError('Некорректный идентификатор заказа.')
        if deadline not in DEADLINES:
            raise ValueError('Выберите срок из списка.')
        note = clean_text(note, 'Комментарий', 1000)
        def create(db):
            db.execute('BEGIN IMMEDIATE')
            return self._create_order(db, user, cart, deadline, note, request_key)
        return await self.run(create)

    def _create_order(self, db, user, cart, deadline, note, request_key):
        existing = db.execute('SELECT * FROM orders WHERE user_id=? AND request_key=?', (user['id'], request_key)).fetchone()
        if existing:
            return self.decode(existing), False
        recent = db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND created_at>=datetime('now','-1 hour')", (user['id'],)).fetchone()[0]
        if recent >= 10:
            raise ValueError('За час можно создать до 10 заказов. Напишите магазину напрямую.')
        items, total = self.quote(db, cart)
        code = 'К-' + secrets.token_hex(4).upper()
        cursor = db.execute('''INSERT INTO orders(code,user_id,username,name,items,total,deadline,note,created_at,request_key)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (code, user['id'], user.get('username', '')[:32],
            str(user.get('first_name', 'Покупатель'))[:100], json.dumps(items, ensure_ascii=False), total, deadline, note,
            datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), request_key))
        return self.decode(db.execute('SELECT * FROM orders WHERE id=?', (cursor.lastrowid,)).fetchone()), True

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    async def create_draft(self, cart, deadline, note):
        if deadline not in DEADLINES:
            raise ValueError('Выберите срок выполнения.')
        note = clean_text(note, 'Комментарий', 1000)
        token = secrets.token_urlsafe(24)
        def create(db):
            db.execute('BEGIN IMMEDIATE')
            self.quote(db, cart)
            now = int(time.time())
            db.execute('DELETE FROM drafts WHERE expires_at<?', (now,))
            if db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0] >= 10000:
                raise ValueError('Сейчас много заказов. Попробуйте чуть позже.')
            db.execute('INSERT INTO drafts(token_hash,payload,expires_at) VALUES(?,?,?)',
                       (self.digest(token), json.dumps({'cart':cart,'deadline':deadline,'note':note},ensure_ascii=False), now+3600))
        await self.run(create)
        return token

    async def redeem_draft(self, token, user):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            raise ValueError('Ссылка заказа недействительна. Соберите заказ на сайте заново.')
        def redeem(db):
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM drafts WHERE token_hash=? AND expires_at>?', (self.digest(token), int(time.time()))).fetchone()
            if not row or (row['user_id'] is not None and row['user_id'] != user['id']):
                raise ValueError('Ссылка заказа истекла или уже использована. Соберите заказ на сайте заново.')
            if row['order_id']:
                return self.decode(db.execute('SELECT * FROM orders WHERE id=?', (row['order_id'],)).fetchone()), False
            data = json.loads(row['payload'])
            order, created = self._create_order(db, user, data['cart'], data['deadline'], data['note'], 'draft_'+token)
            db.execute('UPDATE drafts SET user_id=?,order_id=? WHERE token_hash=?', (user['id'], order['id'], self.digest(token)))
            return order, created
        return await self.run(redeem)

    async def login_start(self):
        token, browser = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        code = f'{secrets.randbelow(1000000):06}'
        def create(db):
            now = int(time.time())
            db.execute('DELETE FROM login_challenges WHERE expires_at<?', (now,))
            db.execute('DELETE FROM sessions WHERE expires_at<?', (now,))
            if db.execute('SELECT COUNT(*) FROM login_challenges').fetchone()[0] >= 5000:
                raise ValueError('Попробуйте войти позже.')
            db.execute('INSERT INTO login_challenges(token_hash,browser_hash,code,expires_at) VALUES(?,?,?,?)',
                       (self.digest(token),self.digest(browser),code,now+300))
        await self.run(create)
        return token, browser, code

    async def login_challenge(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}',token):
            raise ValueError('Ссылка входа недействительна.')
        def get(db):
            row=db.execute('SELECT code FROM login_challenges WHERE token_hash=? AND expires_at>? AND consumed=0', (self.digest(token),int(time.time()))).fetchone()
            if not row:
                raise ValueError('Ссылка входа истекла. Начните вход на сайте заново.')
            return row['code']
        return await self.run(get)

    async def login_approve(self, token, user):
        safe_user = {key:user[key] for key in ('id','first_name','username') if key in user}
        def approve(db):
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM login_challenges WHERE token_hash=? AND expires_at>? AND consumed=0', (self.digest(token),int(time.time()))).fetchone()
            if not row or (row['approved_user'] and json.loads(row['approved_user'])['id'] != user['id']):
                raise ValueError('Ссылка входа недействительна или уже использована.')
            db.execute('UPDATE login_challenges SET approved_user=? WHERE token_hash=?', (json.dumps(safe_user,ensure_ascii=False),self.digest(token)))
        await self.run(approve)

    async def login_finish(self, browser):
        if not browser or len(browser)>100:
            raise ValueError('Начните вход на сайте заново.')
        def finish(db):
            db.execute('BEGIN IMMEDIATE')
            now=int(time.time())
            row=db.execute('SELECT * FROM login_challenges WHERE browser_hash=? AND expires_at>? AND consumed=0', (self.digest(browser),now)).fetchone()
            if not row:
                raise ValueError('Время входа истекло. Попробуйте ещё раз.')
            if not row['approved_user']:
                return None
            session,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (self.digest(session),row['approved_user'],csrf,now+3600))
            db.execute('UPDATE login_challenges SET consumed=1 WHERE browser_hash=?',(self.digest(browser),))
            return session
        return await self.run(finish)

    async def session(self, token):
        if not token or len(token)>100:
            return None
        def get(db):
            row=db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires_at>?', (self.digest(token),int(time.time()))).fetchone()
            return {'user':json.loads(row['user_json']),'csrf':row['csrf']} if row else None
        return await self.run(get)

    async def logout(self, token):
        await self.run(lambda db:db.execute('DELETE FROM sessions WHERE token_hash=?',(self.digest(token),)))

    async def messages(self, order_id):
        return await self.run(lambda db:[dict(row) for row in db.execute('SELECT * FROM messages WHERE order_id=? ORDER BY id DESC LIMIT 100',(order_id,))][::-1])

    async def reply_order(self,user_id,telegram_message_id):
        def find(db):
            row=db.execute("SELECT m.order_id FROM messages m JOIN orders o ON o.id=m.order_id WHERE o.user_id=? AND m.direction='out' AND m.telegram_message_id=? ORDER BY m.id DESC LIMIT 1",(user_id,telegram_message_id)).fetchone()
            return row['order_id'] if row else None
        return await self.run(find)

    async def incoming(self, order_id, user_id, body, request_key):
        body=clean_text(body,'Сообщение',2000,1)
        def save(db):
            row=db.execute('SELECT user_id FROM orders WHERE id=?',(order_id,)).fetchone()
            if not row or row['user_id']!=user_id:
                raise ValueError('Заказ не найден.')
            cursor=db.execute("INSERT OR IGNORE INTO messages(order_id,direction,body,actor_id,state,created_at,request_key) VALUES(?,'in',?,?,'received',datetime('now'),?)",(order_id,body,user_id,request_key))
            return bool(cursor.rowcount)
        return await self.run(save)

    async def prepare_reply(self, order_id, actor_id, body, request_key):
        body=clean_text(body,'Сообщение',2000,1)
        if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_key):
            raise ValueError('Некорректный идентификатор сообщения.')
        def prepare(db):
            db.execute('BEGIN IMMEDIATE')
            order=db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
            if not order:
                raise ValueError('Заказ не найден.')
            existing=db.execute("SELECT * FROM messages WHERE order_id=? AND direction='out' AND request_key=?",(order_id,request_key)).fetchone()
            if existing:
                if existing['body']!=body:
                    raise Conflict('Идентификатор сообщения уже использован. Откройте переписку заново.')
                return dict(existing),dict(order),False
            cursor=db.execute("INSERT INTO messages(order_id,direction,body,actor_id,state,created_at,request_key) VALUES(?,'out',?,?,'sending',datetime('now'),?)",(order_id,body,actor_id,request_key))
            return dict(db.execute('SELECT * FROM messages WHERE id=?',(cursor.lastrowid,)).fetchone()),dict(order),True
        return await self.run(prepare)

    async def finish_reply(self, message_id, state, telegram_id=None):
        await self.run(lambda db:db.execute('UPDATE messages SET state=?,telegram_message_id=? WHERE id=?',(state,telegram_id,message_id)))

    @staticmethod
    def decode(row):
        item = dict(row)
        item['items'] = json.loads(item['items'])
        item.pop('request_key', None)
        return item

    async def orders(self, user_id: int | None = None, limit: int = 50):
        return await self.run(lambda db: [self.decode(row) for row in db.execute(
            'SELECT * FROM orders' + (' WHERE user_id=?' if user_id is not None else '') + ' ORDER BY id DESC LIMIT ?',
            (user_id, limit) if user_id is not None else (limit,))])

    async def order(self, order_id: int):
        def get(db):
            row = db.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            if not row:
                raise ValueError('Заказ не найден.')
            return self.decode(row)
        return await self.run(get)

    async def set_status(self, order_id: int, status: str, actor_id: int | None = None):
        if status not in STATUSES:
            raise ValueError('Неизвестный статус.')
        def update(db):
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM orders WHERE id=?',(order_id,)).fetchone()
            if not row:
                raise ValueError('Заказ не найден.')
            if row['status']==status:
                return None
            db.execute("UPDATE status_events SET state='superseded' WHERE order_id=? AND state='pending'",(order_id,))
            db.execute('UPDATE orders SET status=? WHERE id=?',(status,order_id))
            cursor=db.execute('INSERT INTO status_events(order_id,status,actor_id) VALUES(?,?,?)',(order_id,status,actor_id))
            return cursor.lastrowid
        return await self.run(update)

    async def pending_statuses(self):
        return await self.run(lambda db:[row[0] for row in db.execute("SELECT id FROM status_events WHERE state='pending' AND next_at<=? ORDER BY id LIMIT 20",(int(time.time()),))])

    async def claim_status(self,event_id):
        def claim(db):
            db.execute('BEGIN IMMEDIATE')
            cursor=db.execute("UPDATE status_events SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending' AND next_at<=?",(event_id,int(time.time())))
            if not cursor.rowcount:
                return None
            return dict(db.execute('SELECT e.*,o.user_id,o.code FROM status_events e JOIN orders o ON o.id=e.order_id WHERE e.id=?',(event_id,)).fetchone())
        return await self.run(claim)

    async def finish_status(self,event_id,state,delay=0):
        await self.run(lambda db:db.execute('UPDATE status_events SET state=?,next_at=? WHERE id=?',(state,int(time.time())+delay,event_id)))

    async def recover_statuses(self):
        await self.run(lambda db:db.execute("UPDATE status_events SET state='pending' WHERE state='sending'"))


def order_text(order: dict) -> str:
    lines = [f'Конспектум · заказ {order["code"]}', '']
    for item in order['items']:
        lines.append(f'{item["title"]}: {item["quantity"]} × {money(item["price"])} = {money(item["subtotal"])}')
    lines += ['', f'Предварительно: {money(order["total"])}', f'Срок: {order["deadline"]}']
    if order['note']:
        lines += [f'Комментарий: {order["note"]}']
    lines += ['', 'Хочу согласовать детали и оплату.']
    return '\n'.join(lines)


def contact_url(shop: str, order: dict | None = None) -> str:
    text = order_text(order) if order else 'Здравствуйте! Хочу заказать конспект в Конспектуме.'
    # Telegram links have practical length limits; полный состав остаётся в боте.
    if len(text) > 1500:
        text = f'Здравствуйте! Хочу обсудить и оплатить заказ {order["code"]} на {money(order["total"])}. Полный состав заказа сохранён в боте.'
    return f'https://t.me/{shop}?text={quote(text, safe="")}'
