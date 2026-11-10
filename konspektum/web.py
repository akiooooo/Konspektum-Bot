import json
import hmac
import ipaddress
import logging
from pathlib import Path

from aiohttp import web

from .bot import notify_order
from .delivery import send_reply,send_status
from .security import RateLimiter, authenticate
from .store import Conflict, DEADLINES, STATUSES, contact_url

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
                    if not origin_ok(request) or request.content_type!='application/json':
                        raise web.HTTPForbidden(text='Запрос отклонён. Обновите страницу магазина.')
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
            'Content-Security-Policy': "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org",
            'Cache-Control': 'no-store' if request.path.startswith('/api/') else 'no-cache',
        })
        if config.webapp_url:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    app = web.Application(middlewares=[security], client_max_size=32768)

    async def bootstrap(request):
        s = await store.settings()
        user=request.get(USER)
        is_admin=bool(user and user['id'] in config.admins)
        return web.json_response(dict(services=await store.services(), settings={k: s[k] for k in ('shop_username', 'welcome', 'about')},
                                      deadlines=DEADLINES, statuses=STATUSES, user=user, is_admin=is_admin,
                                      bot_username=config.bot_username, csrf=request.get(CSRF,''),
                                      contact_url=contact_url(s['shop_username'])))

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
        response=web.json_response({'login_url':f'https://t.me/{config.bot_username}?start=login_{token}','code':code,'expires_in':300})
        cookie(response,'k_login',browser,300)
        return response

    async def login_poll(request):
        token=await store.login_finish(request.cookies.get('k_login'))
        response=web.json_response({'authenticated':bool(token)})
        if token:
            cookie(response,'k_session',token,3600)
            response.del_cookie('k_login',path='/')
        return response

    async def logout(request):
        token=request.cookies.get('k_session')
        if token:
            await store.logout(token)
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
        return web.json_response({'order': order, 'contact_url': contact_url(s['shop_username'], order), 'notified': notified}, status=201 if created else 200)

    async def orders(request):
        result = await store.orders(request[USER]['id'])
        s = await store.settings()
        return web.json_response({'orders': [o | {'contact_url': contact_url(s['shop_username'], o)} for o in result]})

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
