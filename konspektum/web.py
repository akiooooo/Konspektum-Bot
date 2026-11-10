import json
import hmac
import ipaddress
import logging
import re
import asyncio
import hashlib
from urllib.parse import parse_qs
from aiogram.types import BufferedInputFile
from aiohttp.helpers import content_disposition_header
from pathlib import Path

from aiohttp import web

from .bot import notify_order
from .delivery import send_reply,send_status
from .security import RateLimiter, authenticate
from .store import Conflict, DEADLINES, STATUSES

STATIC = Path(__file__).parent / 'static'
log = logging.getLogger(__name__)
USER = web.RequestKey('user', dict)
CSRF = web.RequestKey('csrf', str)


def create_app(config, store, bot=None):
    limiter = RateLimiter()

    def client_ip(request):
        remote=request.remote or 'unknown'
        if remote in config.trusted_proxy_ips:
            candidate=request.headers.get('X-Forwarded-For','').split(',')[-1].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        return remote

    def origin_ok(request):
        origin=request.headers.get('Origin')
        expected=config.webapp_url or f'{request.scheme}://{request.host}'
        return origin is None or origin==expected

    async def identity(request):
        raw=request.headers.get('Authorization','')
        if raw:
            if not raw.startswith('tma '):
                raise web.HTTPUnauthorized(text='Откройте магазин заново.')
            try:
                request[USER]=authenticate(raw[4:],config.token)
                auth_date=int(parse_qs(raw[4:])['auth_date'][0])
                if await store.auth_revoked(request[USER]['id'],auth_date):
                    raise ValueError('Выполнен выход со всех устройств. Откройте магазин заново.')
            except (ValueError,TypeError):
                raise web.HTTPUnauthorized(text='Сессия истекла. Переоткройте магазин.')
        else:
            session=await store.session(request.cookies.get('k_session'))
            if session:
                request[USER]=session['user']
                request[CSRF]=session['csrf']

    def cookie(response,name,value,max_age):
        response.set_cookie(name,value,max_age=max_age,httponly=True,secure=bool(config.webapp_url),samesite='Strict',path='/')

    @web.middleware
    async def security(request, handler):
        try:
            if request.path.startswith('/api/'):
                ip=client_ip(request)
                if not limiter.allow(f'ip:{ip}', 240):
                    raise web.HTTPTooManyRequests(text='Слишком много запросов.')
                if request.method not in {'GET','HEAD'}:
                    photo_upload=bool(re.fullmatch(r'/api/admin/orders/\d+/photo',request.path))
                    expected_type='multipart/form-data' if photo_upload else 'application/json'
                    if not origin_ok(request) or request.content_type!=expected_type:
                        raise web.HTTPForbidden(text='Запрос отклонён. Обновите страницу магазина.')
                    if not photo_upload and request.content_length and request.content_length>32768:
                        raise web.HTTPRequestEntityTooLarge(max_size=32768,actual_size=request.content_length)
                    if photo_upload and request.content_length and request.content_length>9*1024*1024:
                        raise ValueError('Фото должно быть не больше 8 МБ.')
                await identity(request)
                public={'/api/bootstrap','/api/drafts','/api/auth/start','/api/auth/poll'}
                if request.path not in public:
                    if USER not in request or config.demo:
                        raise web.HTTPUnauthorized(text='Войдите через Telegram, чтобы продолжить.')
                    uid = request[USER]['id']
                    if not limiter.allow(f'user:{uid}', 100):
                        raise web.HTTPTooManyRequests(text='Слишком много запросов. Подождите минуту.')
                    if request.path.startswith('/api/admin/') and uid not in config.admins:
                        raise web.HTTPForbidden(text='Нет доступа к управлению магазином.')
                if request.method not in {'GET','HEAD'} and CSRF in request:
                    if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),request[CSRF]):
                        raise web.HTTPForbidden(text='Обновите страницу и попробуйте снова.')
                if request.path in {'/api/drafts','/api/auth/start'} and not limiter.allow(f'create:{request.path}:{ip}',20,3600):
                    raise web.HTTPTooManyRequests(text='Слишком много попыток. Попробуйте позже.')
            response = await handler(request)
        except Conflict as exc:
            response = web.json_response({'error': str(exc)}, status=409)
        except (ValueError, KeyError, TypeError) as exc:
            response = web.json_response({'error': str(exc) if isinstance(exc, ValueError) else 'Проверьте введённые данные.'}, status=400)
        except web.HTTPException as exc:
            response = web.json_response({'error': exc.text}, status=exc.status)
        except Exception:
            log.exception('Request failed: %s', request.path)
            response = web.json_response({'error': 'Не удалось выполнить запрос. Попробуйте ещё раз.'}, status=500)
        response.headers.update({
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy': "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org",
            'Cache-Control': 'no-store' if request.path.startswith('/api/') else 'no-cache',
        })
        if config.webapp_url:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    app = web.Application(middlewares=[security], client_max_size=9*1024*1024)

    def bot_chat(order=None):
        return f'https://t.me/{config.bot_username}'+(f'?start=chat_{order["id"]}' if order else '')

    async def bootstrap(request):
        s = await store.settings()
        user=request.get(USER)
        is_admin=bool(user and user['id'] in config.admins)
        return web.json_response(dict(services=await store.services(), settings={k: s[k] for k in ('shop_username', 'welcome', 'about')},
                                      deadlines=DEADLINES, statuses=STATUSES, user=user, is_admin=is_admin,
                                      bot_username=config.bot_username, csrf=request.get(CSRF,''),
                                      contact_url=bot_chat()))

    def require_connected():
        if config.demo or not config.bot_username:
            raise web.HTTPServiceUnavailable(text='Магазин временно недоступен. Попробуйте позже или свяжитесь с нами в Telegram.')

    async def draft(request):
        require_connected()
        data=await request.json()
        if not isinstance(data,dict):
            raise ValueError('Проверьте заказ.')
        token=await store.create_draft(data.get('cart'),data.get('deadline'),data.get('note',''))
        return web.json_response({'bot_url':f'https://t.me/{config.bot_username}?start=order_{token}','expires_in':3600},status=201)

    async def login_start(request):
        require_connected()
        token,browser,code=await store.login_start()
        response=web.json_response({'login_url':f'https://t.me/{config.bot_username}?start=login_{token}','poll_url':f'/api/auth/poll?attempt={token}','code':code,'expires_in':300})
        cookie(response,'k_login_'+token,browser,300)
        return response

    async def login_poll(request):
        # A completed poll can be retried, including after a tab was suspended.
        if USER in request:
            return web.json_response({'authenticated':True})
        attempt=request.query.get('attempt','')
        if attempt and not re.fullmatch(r'[A-Za-z0-9_-]{32}',attempt):
            raise ValueError('Начните вход на сайте заново.')
        name='k_login_'+attempt if attempt else 'k_login'
        token=await store.login_finish(request.cookies.get(name))
        response=web.json_response({'authenticated':bool(token)})
        if token:
            cookie(response,'k_session',token,3600)
            response.del_cookie(name,path='/')
        return response

    async def logout(request):
        await store.logout_all(request[USER]['id'])
        response=web.json_response({'ok':True})
        response.del_cookie('k_session',path='/')
        return response

    async def create_order(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError('Некорректный заказ.')
        order, created = await store.create_order(request[USER], data.get('cart'), data.get('deadline'), data.get('note', ''), data.get('request_key'))
        s = await store.settings()
        notified = await notify_order(bot, store, config, order) if created and bot else None
        return web.json_response({'order': order, 'contact_url': bot_chat(order), 'notified': notified}, status=201 if created else 200)

    async def orders(request):
        result = await store.orders(request[USER]['id'])
        s = await store.settings()
        return web.json_response({'orders': [o | {'contact_url': bot_chat(o)} for o in result]})

    async def admin_data(request):
        return web.json_response({'services': await store.services(True), 'settings': await store.settings(), 'orders': await store.orders(limit=100)})

    async def admin_settings(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError('Некорректные настройки.')
        await store.update_settings(data)
        return web.json_response({'ok': True})

    async def admin_service(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError('Некорректная услуга.')
        sid = int(request.match_info['id']) if 'id' in request.match_info else None
        result = await store.save_service(data, sid)
        return web.json_response({'id': result})

    async def admin_status(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError('Некорректный статус.')
        event_id=await store.set_status(int(request.match_info['id']), data.get('status'),request[USER]['id'])
        notified=await send_status(bot,store,event_id) if event_id else None
        return web.json_response({'ok': True,'notified':notified})

    async def thread(request):
        order=await store.order(int(request.match_info['id']))
        admin=request[USER]['id'] in config.admins
        if not admin and order['user_id']!=request[USER]['id']:
            raise web.HTTPNotFound(text='Заказ не найден.')
        return web.json_response({'order':order,'messages':await store.messages(order['id'])})

    async def reply(request):
        data=await request.json()
        if not isinstance(data,dict):
            raise ValueError('Проверьте сообщение.')
        if not limiter.allow(f'replies:{request[USER]["id"]}',30):
            raise web.HTTPTooManyRequests(text='Слишком много сообщений. Подождите минуту.')
        message=await send_reply(bot,store,int(request.match_info['id']),request[USER]['id'],data.get('body'),data.get('request_key'))
        return web.json_response({'message':message})

    async def delete_order(request):
        data=await request.json()
        if not isinstance(data,dict):
            raise ValueError('Подтвердите удаление заказа.')
        await store.delete_order(int(request.match_info['id']),data.get('confirmation'))
        return web.json_response({'ok':True})

    async def photo_reply(request):
        if not bot:
            raise web.HTTPServiceUnavailable(text='Бот недоступен.')
        if not limiter.allow(f'photo:{request[USER]["id"]}',10):
            raise web.HTTPTooManyRequests(text='Подождите перед следующей отправкой фото.')
        reader=await request.multipart()
        fields={}
        image=None
        async for part in reader:
            if part.name=='photo':
                if image is not None:
                    raise ValueError('Отправляйте по одному фото за запрос.')
                content=bytearray()
                while chunk:=await part.read_chunk():
                    content.extend(chunk)
                    if len(content)>8*1024*1024:
                        raise ValueError('Фото должно быть не больше 8 МБ.')
                image=bytes(content)
            elif part.name in {'body','request_key'}:
                if part.name in fields:
                    raise ValueError('Поле запроса повторяется.')
                value=await part.read_chunk(4096)
                if not part.at_eof():
                    raise ValueError('Слишком длинная подпись.')
                fields[part.name]=value.decode('utf-8')
            else:
                raise ValueError('Неизвестное поле.')
        if not image:
            raise ValueError('Выберите фото.')
        ext='jpg' if image.startswith(b'\xff\xd8\xff') else 'png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'webp' if image.startswith(b'RIFF') and image[8:12]==b'WEBP' else None
        if not ext:
            raise ValueError('Поддерживаются фотографии JPEG, PNG и WebP.')
        attachment={'kind':'photo','name':f'Фото.{ext}','size':len(image),'sha256':hashlib.sha256(image).hexdigest()}
        result=await send_reply(bot,store,int(request.match_info['id']),request[USER]['id'],fields.get('body',''),fields.get('request_key'),attachment,BufferedInputFile(image,filename=f'photo.{ext}'))
        return web.json_response({'message':result})

    async def download(request):
        order=await store.order(int(request.match_info['id']))
        if request[USER]['id'] not in config.admins and request[USER]['id']!=order['user_id']:
            raise web.HTTPNotFound(text='Заказ не найден.')
        row=await store.run(lambda db:db.execute('SELECT attachment FROM messages WHERE id=? AND order_id=?',(int(request.match_info['message']),order['id'])).fetchone())
        attachment=json.loads(row['attachment']) if row and row['attachment'] else None
        if not bot or not attachment or not attachment.get('file_id'):
            raise web.HTTPNotFound(text='Вложение не найдено.')
        if not limiter.allow(f'download:{request[USER]["id"]}',15):
            raise web.HTTPTooManyRequests(text='Подождите перед следующим скачиванием.')
        file=await asyncio.wait_for(bot.get_file(attachment['file_id']),timeout=15)
        if not file.file_size or file.file_size>20*1024*1024:
            raise ValueError('Вложение недоступно для скачивания (лимит 20 МБ).')
        content=await asyncio.wait_for(bot.download_file(file.file_path),timeout=30)
        return web.Response(body=content.getvalue(),content_type='application/octet-stream',headers={'Content-Disposition':content_disposition_header('attachment',filename=attachment.get('name','Вложение')),'Cache-Control':'no-store'})

    async def index(request):
        return web.FileResponse(STATIC / 'index.html')

    async def health(request):
        return web.json_response({'status': 'ok'})

    async def asset(request):
        name = request.match_info['name']
        if name not in {'shop.js', 'style.css', 'favicon.svg','typewriter.svg'}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / name)

    app.router.add_get('/', index)
    app.router.add_get('/assets/{name}', asset)
    app.router.add_get('/health', health)
    app.router.add_get('/api/bootstrap', bootstrap)
    app.router.add_post('/api/drafts',draft)
    app.router.add_post('/api/auth/start',login_start)
    app.router.add_get('/api/auth/poll',login_poll)
    app.router.add_post('/api/auth/logout',logout)
    app.router.add_delete(r'/api/admin/orders/{id:\d+}',delete_order)
    app.router.add_post(r'/api/admin/orders/{id:\d+}/photo',photo_reply)
    app.router.add_get(r'/api/orders/{id:\d+}/messages/{message:\d+}/attachment',download)
    app.router.add_post('/api/orders', create_order)
    app.router.add_get('/api/orders', orders)
    app.router.add_get('/api/admin/data', admin_data)
    app.router.add_patch('/api/admin/settings', admin_settings)
    app.router.add_post('/api/admin/services', admin_service)
    app.router.add_patch('/api/admin/services/{id:\\d+}', admin_service)
    app.router.add_patch('/api/admin/orders/{id:\\d+}', admin_status)
    app.router.add_get('/api/orders/{id:\\d+}/messages',thread)
    app.router.add_get('/api/admin/orders/{id:\\d+}/messages',thread)
    app.router.add_post('/api/admin/orders/{id:\\d+}/messages',reply)
    return app
