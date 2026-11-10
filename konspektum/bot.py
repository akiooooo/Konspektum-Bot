import asyncio
import html
import logging
import secrets

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton

from .config import Config
from .security import RateLimiter
from .notifications import deliver_notice
from .delivery import send_reply,send_status
from .store import DEADLINES, STATUSES, Conflict, Store, money, order_text, price,clean_text

log = logging.getLogger(__name__)
esc = html.escape


def attachment_of(message):
    media=message.photo[-1] if message.photo else message.document
    if media is None:
        return None
    if media.file_size and media.file_size>20*1024*1024:
        raise ValueError('Максимальный размер вложения — 20 МБ.')
    return {'kind':'photo' if message.photo else 'document','file_id':media.file_id,
            'name':'Фото.jpg' if message.photo else (media.file_name or 'Документ'), 'size':media.file_size or 0}


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, **({'url': target} if target.startswith(('https://', 'tg://')) else {'callback_data': target}))
        for label, target in row] for row in rows])


MAIN_MENU=ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text='✎ Каталог'),KeyboardButton(text='🛍 Корзина')],
    [KeyboardButton(text='◷ Мои заказы'),KeyboardButton(text='? Помощь')]],resize_keyboard=True,is_persistent=True,input_field_placeholder='Выберите действие в меню')


class Form(StatesGroup):
    quantity = State()
    note = State()
    admin_value = State()
    customer_message = State()
    admin_reply = State()
    new_title = State()
    new_unit = State()
    new_price = State()
    new_description = State()


class Throttle(BaseMiddleware):
    def __init__(self):
        self.limiter = RateLimiter()

    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        if user and not self.limiter.allow(f'tg:{user.id}', 35, 10):
            if isinstance(event, CallbackQuery):
                await event.answer('Слишком быстро. Подождите немного.', show_alert=True)
            return
        return await handler(event, data)


async def notify_order(bot: Bot, store: Store, config: Config, order: dict):
    text = (f'🟢 <b>Новый заказ {esc(order["code"])}</b>\n'
            f'Клиент: <a href="tg://user?id={order["user_id"]}">{esc(order["name"])}</a>'
            + (f' · @{esc(order["username"])}' if order['username'] else '') + '\n\n' + esc(order_text(order)[:3200]))
    private=keyboard([[('Открыть заказ', f'ao:{order["id"]}')]])
    name=store.config.bot_username
    group=keyboard([[('Открыть заказ в боте',f'https://t.me/{name}?start=manage_{order["id"]}')]]) if name else None
    result=await deliver_notice(bot,store,config,text,private,group)
    if not result['ok']:
        log.warning('Order notification: %s',result['message'])
    return result['delivered']


def make_bot(config: Config, store: Store):
    bot = Bot(config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    router = Router()
    router.message.filter(F.chat.type == 'private')
    router.callback_query.filter(F.message.chat.type == 'private')
    router.message.middleware(Throttle())
    router.callback_query.middleware(Throttle())

    async def show(event, text, rows=None, markup=None):
        markup = markup or keyboard(rows or [])
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as exc:
                if 'message is not modified' not in str(exc):
                    raise
        else:
            await event.answer(text, reply_markup=markup)

    async def home(event):
        settings = await store.settings()
        rows = [[('✎ Каталог услуг', 'catalog:0')], [('🛍 Корзина', 'cart'), ('◷ Мои заказы', 'orders')],
                [('? Помощь', 'about'), ('✉ Чаты заказов', 'orders')]]
        markup = keyboard(rows)
        if config.webapp_url:
            markup.inline_keyboard.append([InlineKeyboardButton(text='Открыть витрину ↗', web_app=WebAppInfo(url=config.webapp_url))])
        if event.from_user.id in config.admins:
            markup.inline_keyboard.append([InlineKeyboardButton(text='⚙ Управление магазином', callback_data='admin')])
        await show(event, '<b>КОНСПЕКТУМ</b>\n<i>Освободим время для важного.</i>\n\n'
                   + esc(settings['welcome'])+'\n\n'
                   + 'Конспекты на машинке по вашим материалам. Аккуратно, с вниманием к каждой странице.\n\n'
                   + '<blockquote>Услуги и объём → удобный срок → готовый заказ</blockquote>\n\n'
                   + 'Выберите услуги — стоимость рассчитаем сразу. Материалы, итоговую цену и оплату обсудим в чате заказа в этом боте.', markup=markup)

    @router.message(CommandStart())
    @router.message(Command('menu', 'cancel'))
    async def start(message: Message, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        await state.update_data(cart=data.get('cart', {}))
        await message.answer('<b>Конспектум</b> · ваше время для важного\nОсновное меню всегда под рукой ↓',reply_markup=MAIN_MENU)
        parts=(message.text or '').split(maxsplit=1)
        payload=parts[1] if len(parts)>1 and parts[0].split('@')[0]=='/start' else ''
        try:
            if payload.startswith('order_'):
                order,created=await store.redeem_draft(payload[6:],message.from_user.model_dump())
                await message.answer('✓ <b>Заказ с сайта создан</b>\nУслуги, количество и пожелания сохранены. Пришлите материалы и вопросы в чат этого заказа.')
                await order_view(message,order)
                await state.update_data(thread_order=order['id'])
                await state.set_state(Form.customer_message)
                if created:
                    await notify_order(bot,store,config,order)
                return
            if payload.startswith('login_'):
                token=payload[6:]
                info=await store.login_info(token)
                if info['approved']:
                    await show(message,'✓ <b>Вход уже подтверждён</b>\n\nВернитесь на страницу сайта, где вы начали вход. Если вы вышли из аккаунта, начните новый вход на сайте.',[[('В магазин','home')]])
                    return
                code=info['code']
                await show(message,f'<b>Вход в Конспектум</b>\n\nСайт: {esc(config.webapp_url or "локальный сайт магазина")}\nКод на сайте: <code>{code}</code>\n\n'
                    'Сравните его с кодом в открытом вами браузере. Подтвердите вход только если вы сами начали его на сайте магазина. '
                    'Не подтверждайте ссылки, присланные другими людьми.\n\nСсылка действует 5 минут.',
                    [[('Подтвердить вход',f'login:{token}')],[('Отмена','home')]])
                return
            if payload.startswith('manage_'):
                if message.from_user.id not in config.admins:
                    raise ValueError('Нет доступа к управлению заказами.')
                await order_view(message,await store.order(int(payload[7:])),True)
                return
            if payload.startswith(('view_','chat_')):
                order=await store.order(int(payload[5:]))
                if order['user_id']!=message.from_user.id:
                    raise ValueError('Заказ не найден.')
                await order_view(message,order)
                if payload.startswith('chat_'):
                    await state.update_data(thread_order=order['id'])
                    await state.set_state(Form.customer_message)
                    await message.answer('Отправьте сообщение, фото или документ для этого заказа. Ответ магазина придёт сюда.')
                return
        except ValueError as exc:
            await message.answer(esc(str(exc)))
        await home(message)

    @router.message(Command('id'))
    async def identify(message: Message):
        await message.answer(f'Ваш Telegram ID: <code>{message.from_user.id}</code>')

    async def admin_home(event):
        await show(event, '<b>Управление · Конспектум</b>\n\nУслуги, настройки и последние заказы.', [
            [('✎ Услуги и цены', 'as:0'), ('＋ Добавить услугу', 'aa')],
            [('◷ Все заказы', 'aorders'), ('⚙ Настройки', 'settings')], [('← В магазин', 'home')]])

    @router.message(Command('admin'))
    async def admin_command(message: Message, state: FSMContext):
        if message.from_user.id not in config.admins:
            await message.answer('Нет доступа к управлению магазином.')
            return
        await state.set_state(None)
        await admin_home(message)

    async def catalog(event, page=0, admin=False):
        services = await store.services(admin)
        page = max(0, min(page, max(0, (len(services) - 1) // 8)))
        rows = [[(f'{"○ " if not s["active"] else ""}{s["icon"]} {s["title"]} · {money(s["price"])} / {s["unit"]}', f'{"ae" if admin else "svc"}:{s["id"]}')] for s in services[page * 8:(page + 1) * 8]]
        nav = []
        if page:
            nav.append(('←', f'{"as" if admin else "catalog"}:{page - 1}'))
        if (page + 1) * 8 < len(services):
            nav.append(('→', f'{"as" if admin else "catalog"}:{page + 1}'))
        if nav:
            rows.append(nav)
        rows.append([('← Управление' if admin else '🛍 Мой заказ', 'admin' if admin else 'cart')])
        rows.append([('Главное меню', 'home')])
        await show(event, '<b>КОНСПЕКТУМ / КАТАЛОГ</b>\n\n' + ('○ — услуга скрыта от покупателей.' if admin else '<b>01 · Что напишем?</b>\nВыберите услугу и укажите объём. Можно добавить несколько услуг в один заказ.\n\n<blockquote>Расчёт предварительный. Итоговую стоимость согласуем по вашим материалам.</blockquote>') + ('' if services else '\n\nКаталог пока пуст. Напишите магазину через главное меню.'), rows)

    async def cart_view(event, state):
        data = await state.get_data()
        cart = data.get('cart', {})
        services = {str(s['id']): s for s in await store.services()}
        clean = {k: min(v, services[k]['max_quantity']) for k, v in cart.items() if k in services}
        await state.update_data(cart=clean)
        if not clean:
            await show(event, '<b>Ваш заказ пока пуст</b>\n\nНачнём с конспекта? Выберите услугу в каталоге.', [[('✎ Выбрать услуги', 'catalog:0')], [('← Главное меню', 'home')]])
            return
        lines, total, rows, quote = [], 0, [], []
        for index, (key, quantity) in enumerate(clean.items()):
            s = services[key]
            subtotal = s['price'] * quantity
            if index < 20:
                lines.append(f'{s["title"]} · {quantity} × {money(s["price"])} = {money(subtotal)}')
            total += subtotal
            if index < 20:
                rows.append([(f'✕ {s["title"][:30]}', f'del:{key}')])
            quote.append(dict(id=int(key), quantity=quantity, price=s['price']))
        await state.update_data(quote=quote, ready_key=None)
        if len(clean) > 20:
            lines.append(f'И ещё {len(clean) - 20} позиций. Полный состав сохранится в заказе.')
        rows = [[('Продолжить →', 'checkout')], [('＋ Добавить услуги', 'catalog:0')]] + rows + [[('Очистить', 'clear'), ('← Меню', 'home')]]
        await show(event, '<b>Ваш заказ</b>\n\n' + esc('\n'.join(lines)[:2900]) + f'\n\nПредварительно: <b>{money(total)}</b>\nОплата после согласования в чате заказа.', rows)

    async def order_view(event, order, admin=False):
        settings = await store.settings()
        rows = []
        if admin:
            rows += [[('✉ Ответить клиенту', f'areply:{order["id"]}')]]
            rows += [[(label, f'status:{order["id"]}:{key}')] for key, label in STATUSES.items()]
            rows += [[('Удалить заказ…', f'delorder:{order["id"]}')]]
            rows += [[('← Все заказы', 'aorders')]]
        else:
            rows += [[('✉ Обсудить заказ в боте',f'chat:{order["id"]}')], [('← Мои заказы', 'orders')], [('← Главное меню', 'home')]]
        text = f'<b>{esc(order["code"])} · {STATUSES[order["status"]]}</b>\n\n' + esc(order_text(order)[:3300])
        if admin:
            text += f'\n\nКлиент: <a href="tg://user?id={order["user_id"]}">{esc(order["name"])}</a> · <code>{order["user_id"]}</code>'
            if order['username']:
                text += f' · @{esc(order["username"])}'
        await show(event, text, rows)

    async def callbacks(event: CallbackQuery, state: FSMContext):
        action, *parts = (event.data or '').split(':')
        admin_actions = {'admin', 'as', 'ae', 'aa', 'af', 'toggle', 'aorders', 'ao', 'settings', 'setting', 'status','delorder','confirmdelete','testnotify','envnotify','areply','anunit','ancustom','andesc','ancreate'}
        if action in admin_actions and event.from_user.id not in config.admins:
            await event.answer('Нет доступа.', show_alert=True)
            return
        await event.answer()
        if action not in {'deadline', 'skip', 'place','anunit','ancustom','andesc','ancreate'}:
            await state.set_state(None)
        if action == 'home':
            await home(event)
        elif action=='login':
            await store.login_approve(parts[0],event.from_user.model_dump())
            await show(event,'✓ <b>Вход подтверждён</b>\n\nВернитесь в тот браузер, где вы начали вход. Страница обновится автоматически.',[[('В магазин','home')]])
        elif action=='delorder':
            order=await store.order(int(parts[0]))
            nonce=secrets.token_urlsafe(12)
            await state.update_data(delete_order=order['id'],delete_code=order['code'],delete_nonce=nonce)
            await show(event,f'<b>Удалить заказ {esc(order["code"])}?</b>\n\nЗаказ и сохранённая переписка будут удалены. Отправленные сообщения в Telegram останутся. Отменить удаление нельзя.',
                [[('Да, удалить',f'confirmdelete:{order["id"]}:{nonce}')],[('Отмена',f'ao:{order["id"]}')]])
        elif action=='confirmdelete':
            data=await state.get_data()
            if len(parts)!=2 or data.get('delete_order')!=int(parts[0]) or not secrets.compare_digest(data.get('delete_nonce',''),parts[1]):
                raise ValueError('Откройте подтверждение удаления заново.')
            await store.delete_order(int(parts[0]),data['delete_code'])
            await state.update_data(delete_nonce='')
            await show(event,'✓ Заказ и его переписка удалены.',[[('К заказам','aorders')]])
        elif action in {'chat','areply'}:
            order=await store.order(int(parts[0]))
            if action=='chat' and order['user_id']!=event.from_user.id:
                raise ValueError('Заказ не найден.')
            await state.update_data(thread_order=order['id'],reply_key=secrets.token_urlsafe(18))
            await state.set_state(Form.admin_reply if action=='areply' else Form.customer_message)
            messages=await store.messages(order['id'])
            excerpt='\n\n'.join(('Магазин' if m['direction']=='out' else 'Покупатель')+': '+m['body'][:300] for m in messages[-4:])
            await show(event,f'<b>Переписка · {esc(order["code"])}</b>\n\n'+(esc(excerpt)+'\n\n' if excerpt else '')+'Отправьте текст сообщения (до 2000 символов).\nЗдесь можно отправить текст, фото или документ (до 20 МБ).\n\n/cancel — отменить.',[[('← К заказу',f'{"ao" if action=="areply" else "order"}:{order["id"]}')]])
        elif action in {'catalog', 'as'}:
            await catalog(event, int(parts[0]), action == 'as')
        elif action in {'svc', 'ae'}:
            services = {s['id']: s for s in await store.services(action == 'ae')}
            s = services.get(int(parts[0]))
            if not s:
                raise ValueError('Услуга недоступна. Откройте каталог заново.')
            if action == 'ae':
                rows = [[('Название', f'af:{s["id"]}:title'), ('Цена', f'af:{s["id"]}:price')],
                        [('Описание', f'af:{s["id"]}:description'), ('Единица', f'af:{s["id"]}:unit')],
                        [('Лимит количества', f'af:{s["id"]}:max_quantity'), ('Значок', f'af:{s["id"]}:icon')],
                        [('Скрыть услугу' if s['active'] else 'Вернуть в каталог', f'toggle:{s["id"]}')], [('← Услуги', 'as:0')]]
            else:
                data=await state.get_data()
                current=data.get('cart',{}).get(str(s['id']),0)
                rows = [[('＋ 1', f'add:{s["id"]}:1'), ('＋ 5', f'add:{s["id"]}:5'), ('＋ 10', f'add:{s["id"]}:10')],
                        [('− 1',f'add:{s["id"]}:-1')],
                        [('Указать количество', f'qty:{s["id"]}')], [('🛍 Мой заказ', 'cart'), ('← Услуги', 'catalog:0')]]
            await show(event, f'{esc(s["icon"])} <b>{esc(s["title"])}</b>\n\n{esc(s["description"])}\n\n<blockquote>{money(s["price"])} / {esc(s["unit"])} · до {s["max_quantity"]} за заказ</blockquote>'+ (f'\n\nВ корзине: <b>{current}</b>' if action=='svc' else ''), rows)
        elif action in {'add', 'qty'}:
            service_id = int(parts[0])
            s = next((s for s in await store.services() if s['id'] == service_id), None)
            if not s:
                raise ValueError('Услуга недоступна.')
            if action == 'qty':
                await state.update_data(service_id=service_id)
                await state.set_state(Form.quantity)
                await show(event, f'Сколько нужно: <b>{esc(s["title"])}</b>?\nОтправьте число от 1 до {s["max_quantity"]}. Оно заменит текущее количество.\n\n/cancel — вернуться в меню.')
            else:
                data = await state.get_data()
                cart = dict(data.get('cart', {}))
                key = str(service_id)
                delta=int(parts[1])
                if delta not in {-1,1,5,10}:
                    raise ValueError('Откройте карточку услуги заново.')
                cart[key] = max(0,min(cart.get(key, 0) + delta, s['max_quantity']))
                if cart[key]==0:
                    cart.pop(key)
                await state.update_data(cart=cart)
                await cart_view(event, state)
        elif action == 'cart':
            await cart_view(event, state)
        elif action in {'del', 'clear'}:
            data = await state.get_data()
            cart = dict(data.get('cart', {})) if action == 'del' else {}
            if action == 'del':
                cart.pop(parts[0], None)
            await state.update_data(cart=cart)
            await cart_view(event, state)
        elif action == 'checkout':
            data = await state.get_data()
            if not data.get('quote') or not data.get('cart'):
                await cart_view(event, state)
                return
            await state.update_data(request_key=secrets.token_urlsafe(18), ready_key=None)
            await show(event, '<b>КОНСПЕКТУМ / ОФОРМЛЕНИЕ</b>\n\n<b>02 · Когда нужен конспект?</b>\n\n<blockquote>Выберите желаемый срок. Возможность срочного выполнения и итоговую дату подтвердим в чате заказа.</blockquote>', [[(d, f'deadline:{i}')] for i, d in enumerate(DEADLINES)] + [[('← К заказу', 'cart')]])
        elif action == 'deadline':
            index = int(parts[0])
            if not 0 <= index < len(DEADLINES):
                raise ValueError('Выберите срок заново.')
            await state.update_data(deadline=DEADLINES[index])
            await state.set_state(Form.note)
            await show(event, '<b>КОНСПЕКТУМ / ОФОРМЛЕНИЕ</b>\n\n<b>03 · Добавим детали?</b>\nНапишите предмет, формат тетради и пожелания (до 1000 символов).\n\n<blockquote>Материалы отправите сюда, в чат заказа после оформления. Платёжные данные в боте не нужны.</blockquote>', [[('Без комментария →', 'skip')], [('← К заказу', 'cart')]])
        elif action == 'skip':
            if await state.get_state() != Form.note.state:
                raise ValueError('Откройте оформление заказа заново.')
            await state.set_state(None)
            await state.update_data(note='')
            await confirm(event, state)
        elif action == 'place':
            data = await state.get_data()
            if not all(k in data for k in ('quote', 'deadline', 'note', 'request_key')) or not parts or parts[0] != data.get('ready_key'):
                raise ValueError('Откройте корзину и оформите заказ заново.')
            try:
                order, created = await store.create_order(event.from_user.model_dump(), data['quote'], data['deadline'], data['note'], data['request_key'])
            except Conflict:
                await cart_view(event, state)
                await event.message.answer('Условия изменились. Проверьте корзину и подтвердите заказ ещё раз.')
                return
            await state.update_data(cart={})
            await order_view(event, order)
            await state.update_data(thread_order=order['id'])
            await state.set_state(Form.customer_message)
            if created:
                await notify_order(bot, store, config, order)
        elif action in {'orders', 'aorders'}:
            orders = await store.orders(None if action == 'aorders' else event.from_user.id, 20)
            rows = [[(f'{o["code"]} · {money(o["total"])} · {STATUSES[o["status"]]}', f'{"ao" if action == "aorders" else "order"}:{o["id"]}')] for o in orders]
            rows += [[('← Назад', 'admin' if action == 'aorders' else 'home')]]
            await show(event, '<b>Последние заказы</b>\n\n' + ('Выберите заказ.' if orders else 'Пока ни одного заказа.'), rows)
        elif action in {'order', 'ao'}:
            order = await store.order(int(parts[0]))
            if action == 'order' and order['user_id'] != event.from_user.id:
                raise ValueError('Заказ не найден.')
            await order_view(event, order, action == 'ao')
        elif action == 'about':
            settings = await store.settings()
            await show(event, '<b>Освободите время для важного</b>\n\n1. Выберите услуги и объём.\n2. Оставьте пожелания по срокам.\n3. Откройте чат заказа в боте, пришлите материалы и согласуйте оплату.\n\n' + esc(settings['about']) + '\n\nХраним ваш Telegram ID, имя и сведения о заказе для его выполнения. Не присылайте в бота реквизиты карт и пароли.', [[('✎ Выбрать услуги', 'catalog:0')], [('← Меню', 'home')]])
        elif action == 'admin':
            await admin_home(event)
        elif action == 'aa':
            await state.set_state(Form.new_title)
            await state.update_data(draft_service={})
            await show(event,'<b>Новая услуга · 1 из 4</b>\n\nКак она называется? Отправьте название до 80 символов.\n\n/cancel — отменить.')
        elif action=='anunit':
            if await state.get_state()!=Form.new_unit.state:
                raise ValueError('Начните добавление услуги заново.')
            units={'page':'страница','sheet':'лист','item':'элемент'}
            unit=units.get(parts[0])
            if not unit:
                raise ValueError('Выберите единицу из меню.')
            data=await state.get_data()
            await state.update_data(draft_service=data['draft_service']|{'unit':unit})
            await state.set_state(Form.new_price)
            await show(event,'<b>Новая услуга · 3 из 4</b>\n\nСколько стоит одна единица? Отправьте цену в рублях, например 45 или 45,50.\n\n/cancel — отменить.')
        elif action=='ancustom':
            if await state.get_state()!=Form.new_unit.state:
                raise ValueError('Начните добавление услуги заново.')
            await show(event,'Напишите единицу измерения, например «разворот» (до 30 символов).')
        elif action=='andesc':
            if await state.get_state()!=Form.new_description.state:
                raise ValueError('Начните добавление услуги заново.')
            data=await state.get_data()
            await state.update_data(draft_service=data['draft_service']|{'description':''})
            await state.set_state(None)
            await review_service(event,state)
        elif action=='ancreate':
            data=await state.get_data()
            draft=data.get('draft_service')
            if not isinstance(draft,dict) or not {'title','unit','price','description'}<=draft.keys():
                raise ValueError('Начните добавление услуги заново.')
            await store.save_service(draft)
            await state.update_data(draft_service=None)
            await state.set_state(None)
            await show(event,'✓ <b>Услуга добавлена</b>\nОна доступна и в Telegram, и на сайте. Значок и лимит количества можно изменить в её карточке.',[[('Открыть услуги','as:0')],[('Управление','admin')]])
        elif action == 'af':
            if parts[1] not in {'title', 'price', 'description', 'unit', 'max_quantity', 'icon'}:
                raise ValueError('Неизвестное поле.')
            await state.set_state(Form.admin_value)
            await state.update_data(admin_action='service', edit_id=int(parts[0]), field=parts[1])
            await show(event, 'Отправьте новое значение. Цена — в рублях.\n/cancel — отменить.')
        elif action == 'toggle':
            s = next((s for s in await store.services(True) if s['id'] == int(parts[0])), None)
            if not s:
                raise ValueError('Услуга не найдена.')
            await store.save_service(s | {'active': not s['active']}, s['id'])
            await catalog(event, admin=True)
        elif action == 'settings':
            s = await store.settings()
            await show(event, f'<b>Настройки магазина</b>\n\nКонтакт владельца: @{esc(s["shop_username"])}\nУведомления: {esc(s["notify_chat_id"]) or "администраторам"}\nВ .env: {esc(config.notify_chat_id) or "администраторам"}\nПриветствие: {esc(s["welcome"])}', [
                [('Контакт магазина', 'setting:shop_username')], [('Чат уведомлений', 'setting:notify_chat_id')],[('Проверить уведомления','testnotify')],[('Загрузить чат из .env','envnotify')],
                [('Приветствие', 'setting:welcome'), ('О магазине', 'setting:about')], [('← Управление', 'admin')]])
        elif action=='testnotify':
            result=await deliver_notice(bot,store,config,'✓ Конспектум: проверка уведомлений. Новые заказы и сообщения будут приходить в этот чат.')
            await event.message.answer(esc(result['message']))
        elif action=='envnotify':
            await store.update_settings({'notify_chat_id':config.notify_chat_id})
            await event.message.answer('✓ Чат уведомлений загружен из .env. Нажмите «Проверить уведомления».')
        elif action == 'setting':
            if parts[0] not in {'shop_username', 'notify_chat_id', 'welcome', 'about'}:
                raise ValueError('Неизвестная настройка.')
            await state.set_state(Form.admin_value)
            await state.update_data(admin_action='setting', field=parts[0])
            await show(event, 'Отправьте новое значение.\n\nКонтакт: @username. Чат уведомлений: числовой ID; «-» — сбросить и отправлять администраторам. Бот должен иметь доступ к этому чату.\n/cancel — отменить.')
        elif action == 'status':
            event_id=await store.set_status(int(parts[0]), parts[1],event.from_user.id)
            await order_view(event, await store.order(int(parts[0])), True)
            if event_id:
                delivered=await send_status(bot,store,event_id)
                await event.message.answer('✓ Покупатель получил уведомление.' if delivered else 'Статус сохранён. Уведомление отправим при восстановлении связи, если покупатель не заблокировал бота.')

    async def confirm(event, state):
        data = await state.get_data()
        if not data.get('quote') or not data.get('deadline'):
            raise ValueError('Откройте корзину и начните оформление.')
        items, total = await store.run(lambda db: store.quote(db, data['quote']))
        text = '<b>Всё верно?</b>\n\n' + esc('\n'.join(f'{i["title"]} · {i["quantity"]} × {money(i["price"])}' for i in items)[:1800])
        text += f'\n\nПредварительно: <b>{money(total)}</b>\nСрок: {esc(data["deadline"])}'
        if data.get('note'):
            text += '\n' + esc(data['note'])
        text += '\n\nСоздавая заказ, вы передаёте магазину своё имя, Telegram ID и данные заказа для его выполнения. Оплата и окончательные условия — в чате заказа в боте.'
        await state.update_data(ready_key=data['request_key'])
        await show(event, text, [[('Создать заказ →', f'place:{data["request_key"]}')], [('← Изменить заказ', 'cart')]])

    @router.message(F.text.in_({'✎ Каталог','🛍 Корзина','◷ Мои заказы','? Помощь'}))
    async def navigation(message:Message,state:FSMContext):
        await state.set_state(None)
        if message.text=='✎ Каталог':
            await catalog(message)
        elif message.text=='🛍 Корзина':
            await cart_view(message,state)
        elif message.text=='◷ Мои заказы':
            orders=await store.orders(message.from_user.id,20)
            rows=[[(f'{o["code"]} · {money(o["total"])} · {STATUSES[o["status"]]}',f'order:{o["id"]}')] for o in orders]
            await show(message,'<b>КОНСПЕКТУМ / МОИ ЗАКАЗЫ</b>\n\n'+('Выберите заказ, чтобы увидеть детали или написать нам.' if orders else 'Здесь появятся ваши заказы.'),rows+[[('← В магазин','home')]])
        else:
            s=await store.settings()
            await show(message,'<b>КОНСПЕКТУМ / ПОМОЩЬ</b>\n\n<b>Как заказать?</b>\nВыберите услуги и количество → срок → пожелания. После оформления пришлите материалы в чат заказа в боте.\n\n<b>Как оплатить?</b>\nСогласуем стоимость и способ оплаты лично. В боте нет платёжных форм.\n\n<b>Как считать страницы?</b>\nУкажите примерный объём. Формат тетради, плотность записи и окончательный объём обсудим по материалам.\n\n<b>Как связаться?</b>\nКнопкой «Обсудить заказ в боте».',[[('Чаты заказов','orders')],[('Выбрать услуги','catalog:0')]])

    @router.callback_query()
    async def dispatch_callback(event: CallbackQuery, state: FSMContext):
        try:
            await callbacks(event, state)
        except (ValueError, IndexError, KeyError) as exc:
            text = str(exc) if isinstance(exc, ValueError) else 'Кнопка устарела. Откройте /menu.'
            await event.message.answer(esc(text))

    @router.message(Form.quantity, F.text)
    async def quantity_input(message: Message, state: FSMContext):
        try:
            data = await state.get_data()
            s = next((s for s in await store.services() if s['id'] == data['service_id']), None)
            if not s or not message.text.isascii() or not message.text.isdigit() or len(message.text) > 4 or not 1 <= int(message.text) <= s['max_quantity']:
                raise ValueError('Введите допустимое целое число. /cancel — отменить.')
            cart = dict(data.get('cart', {}))
            cart[str(s['id'])] = int(message.text)
            await state.update_data(cart=cart)
            await state.set_state(None)
            await cart_view(message, state)
        except ValueError as exc:
            await message.answer(esc(str(exc)))

    @router.message(Form.note, F.text)
    async def note_input(message: Message, state: FSMContext):
        if len(message.text) > 1000:
            await message.answer('Сократите комментарий до 1000 символов.')
            return
        await state.update_data(note=message.text)
        await state.set_state(None)
        try:
            await confirm(message, state)
        except ValueError as exc:
            await message.answer(esc(str(exc)), reply_markup=keyboard([[('Обновить корзину', 'cart')]]))

    @router.message(Form.admin_value, F.text)
    async def admin_input(message: Message, state: FSMContext):
        if message.from_user.id not in config.admins:
            await state.clear()
            return
        data = await state.get_data()
        try:
            if data['admin_action'] == 'setting':
                await store.update_settings({data['field']: '' if data['field'] == 'notify_chat_id' and message.text.strip() == '-' else message.text})
            else:
                s = next((s for s in await store.services(True) if s['id'] == data['edit_id']), None)
                if not s:
                    raise ValueError('Услуга не найдена.')
                field = data['field']
                value = price(message.text) if field == 'price' else int(message.text) if field == 'max_quantity' else message.text
                await store.save_service(s | {field: value}, s['id'])
            await state.set_state(None)
            await message.answer('✓ Изменения сохранены.')
            await admin_home(message)
        except ValueError as exc:
            await message.answer(esc(str(exc)) + '\nПопробуйте ещё раз или /cancel.')

    async def review_service(event,state):
        s=(await state.get_data())['draft_service']
        await show(event,f'<b>Проверим новую услугу</b>\n\n{esc(s["title"])}\n{money(s["price"])} / {esc(s["unit"])}\n\n{esc(s["description"])}',
            [[('Добавить в каталог','ancreate')],[('Начать заново','aa'),('Отмена','admin')]])

    @router.message(StateFilter(Form.new_title,Form.new_unit,Form.new_price,Form.new_description),F.text)
    async def new_service_input(message:Message,state:FSMContext):
        if message.from_user.id not in config.admins:
            await state.clear()
            return
        current=await state.get_state()
        data=await state.get_data()
        draft=data.get('draft_service') or {}
        try:
            if current==Form.new_title.state:
                draft['title']=clean_text(message.text,'Название',80,1)
                await state.update_data(draft_service=draft)
                await state.set_state(Form.new_unit)
                await show(message,'<b>Новая услуга · 2 из 4</b>\n\nЗа что указывается цена? Выберите единицу или напишите свою.',
                    [[('Страница','anunit:page'),('Лист','anunit:sheet'),('Элемент','anunit:item')],[('Свой вариант','ancustom')]])
            elif current==Form.new_unit.state:
                draft['unit']=clean_text(message.text,'Единица',30,1)
                await state.update_data(draft_service=draft)
                await state.set_state(Form.new_price)
                await message.answer('<b>Новая услуга · 3 из 4</b>\n\nОтправьте цену за единицу в рублях.')
            elif current==Form.new_price.state:
                draft['price']=price(message.text)
                await state.update_data(draft_service=draft)
                await state.set_state(Form.new_description)
                await show(message,'<b>Новая услуга · 4 из 4</b>\n\nКоротко опишите услугу (до 400 символов).',[[('Без описания','andesc')]])
            else:
                draft['description']=clean_text(message.text,'Описание',400)
                await state.update_data(draft_service=draft)
                await state.set_state(None)
                await review_service(message,state)
        except ValueError as exc:
            await message.answer(esc(str(exc))+'\nПопробуйте ещё раз или /cancel.')

    @router.message(Form.customer_message,F.text | F.photo | F.document)
    async def customer_message(message:Message,state:FSMContext):
        data=await state.get_data()
        try:
            order_id=data['thread_order']
            attachment=attachment_of(message)
            body=message.text or message.caption or ''
            fresh=await store.incoming(order_id,message.from_user.id,body,f'tg_{message.chat.id}_{message.message_id}',attachment)
            order=await store.order(order_id)
            await state.set_state(Form.customer_message)
            await message.answer(f'✓ Отправлено · {esc(order["code"])}. Можно отправить ещё сообщение или фото.',reply_markup=keyboard([[('К заказу',f'order:{order_id}')]]))
            if fresh:
                result=await deliver_notice(bot,store,config,f'<b>Сообщение · {esc(order["code"])}</b>\n\n{esc(body) or "Вложение"}',
                    keyboard([[('Ответить',f'areply:{order_id}')]]),
                    keyboard([[('Открыть чат заказа',f'https://t.me/{store.config.bot_username}?start=manage_{order_id}')]]) if store.config.bot_username else None)
                if attachment:
                    async def notify_attachment(destination):
                        try:
                            method=bot.send_photo if attachment['kind']=='photo' else bot.send_document
                            field='photo' if attachment['kind']=='photo' else 'document'
                            await asyncio.wait_for(method(destination,**{field:attachment['file_id']},caption=f'Материалы · {esc(order["code"])}'),timeout=15)
                        except (TelegramAPIError,TimeoutError):
                            log.warning('Attachment notification unavailable for order %s',order['code'])
                    await asyncio.gather(*(notify_attachment(x) for x in result['delivered_to']))
        except ValueError as exc:
            await message.answer(esc(str(exc)))

    @router.message(Form.admin_reply,F.text | F.photo | F.document)
    async def admin_reply(message:Message,state:FSMContext):
        if message.from_user.id not in config.admins:
            await state.clear()
            return
        data=await state.get_data()
        try:
            result=await send_reply(bot,store,data['thread_order'],message.from_user.id,message.text or message.caption or '',data['reply_key'],attachment_of(message))
            await state.update_data(reply_key=secrets.token_urlsafe(18))
            labels={'sent':'✓ Ответ доставлен в Telegram покупателя.','failed':'Сообщение не доставлено. Возможно, покупатель заблокировал бота.',
                    'unknown':'Telegram не подтвердил доставку. Сообщение могло дойти; проверьте переписку перед повторной отправкой.',
                    'sending':'Отправка уже выполняется. Проверьте переписку позже.'}
            await message.answer(labels[result['state']])
            await message.answer('Можно отправить следующий ответ или фото. /cancel — выйти из чата.')
        except ValueError as exc:
            await message.answer(esc(str(exc)))

    @router.message(F.reply_to_message,F.text | F.photo | F.document)
    async def direct_reply(message:Message,state:FSMContext):
        order_id=await store.reply_order(message.from_user.id,message.reply_to_message.message_id)
        if order_id is not None:
            await state.update_data(thread_order=order_id)
            await customer_message(message,state)
        else:
            await message.answer('Откройте «Мои заказы» и нажмите «Обсудить заказ в боте», чтобы сообщение попало в нужную переписку.')

    @router.message()
    async def fallback(message: Message,state:FSMContext):
        if message.text or message.photo or message.document:
            orders=await store.orders(message.from_user.id,2)
            if len(orders)==1 and message.from_user.id not in config.admins:
                await state.update_data(thread_order=orders[0]['id'])
                await customer_message(message,state)
                return
            if orders:
                await message.answer('Выберите заказ, к которому относится сообщение, затем отправьте его ещё раз.',reply_markup=keyboard([[('Мои заказы','orders')]]))
                return
        await message.answer('Выберите действие в меню. Материалы и файлы отправьте в чат заказа в этом боте.\n/menu — открыть магазин\n/cancel — отменить ввод')

    group_router=Router()

    @group_router.message(F.chat.type.in_({'group','supergroup'}),Command('chatid','id'))
    async def group_id(message:Message):
        if message.from_user and message.from_user.id in config.admins:
            await message.answer(f'ID этой группы: <code>{message.chat.id}</code>\nУкажите его в настройках чата уведомлений и нажмите «Проверить уведомления».')

    dp.include_router(group_router)
    dp.include_router(router)
    return bot, dp


async def setup_commands(bot: Bot):
    await bot.set_my_name(name='Конспектум', language_code='ru')
    await bot.set_my_description(description='Аккуратные конспекты на машинке по вашим материалам. Выберите услуги и объём, рассчитайте стоимость и обсудите детали в чате заказа в боте. Оплата после согласования.', language_code='ru')
    await bot.set_my_short_description(short_description='Конспекты на заказ. Меньше рутины — больше жизни.', language_code='ru')
    await bot.set_my_commands([BotCommand(command='start', description='Открыть Конспектум'), BotCommand(command='menu', description='Главное меню'),
                               BotCommand(command='cancel', description='Отменить ввод'), BotCommand(command='id', description='Мой Telegram ID'), BotCommand(command='chatid', description='ID группы для уведомлений'),
                               BotCommand(command='admin', description='Управление магазином')])
