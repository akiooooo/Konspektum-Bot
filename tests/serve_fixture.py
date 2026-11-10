"""Изолированный сервер для проверки интерфейса. Никаких реальных сообщений и рабочей базы."""
import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from datetime import datetime,timezone

from aiohttp import web
from aiogram.types import Message,Chat

from konspektum.store import Store
from konspektum.web import create_app,STATIC
from tests.helpers import config,signed


async def main():
    with tempfile.TemporaryDirectory() as directory:
        cfg=replace(config(directory),bot_username='test_shop_bot')
        store=Store(cfg)
        await store.initialize()
        for index,details in enumerate([('Анна','Физика','in_progress'),('Михаил','История','new'),('София','Биология','done')]):
            name,note,status=details
            order,_=await store.create_order({'id':200+index,'first_name':name,'username':'test_buyer'},[{'id':1,'quantity':12+index*4,'price':4500}],'В течение недели',note,f'fixture_order_{index}')
            await store.set_status(order['id'],status,100)
            await store.incoming(order['id'],200+index,'Здравствуйте! Подскажите, можно добавить заголовки?',f'fixture_{index}')
        bot=AsyncMock()
        bot.send_message.return_value=Message(message_id=99,date=datetime.now(timezone.utc),chat=Chat(id=200,type='private'))
        app=create_app(cfg,store,bot)

        @web.middleware
        async def fixture(request,handler):
            if request.path=='/':
                text=(STATIC/'index.html').read_text(encoding='utf-8').replace('https://telegram.org/js/telegram-web-app.js','/test/telegram.js')
                return web.Response(text=text,content_type='text/html')
            return await handler(request)

        async def telegram(request):
            # Тестовая подпись известного тестового бота; отсутствует в рабочем приложении.
            return web.Response(text='window.Telegram={WebApp:{initData:'+repr(signed(100))+',ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){}}};',content_type='text/javascript')

        app.middlewares.insert(0,fixture)
        app.router.add_get('/test/telegram.js',telegram)
        runner=web.AppRunner(app,access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner,'127.0.0.1',8092).start()
            print('Isolated interface fixture: http://127.0.0.1:8092',flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__=='__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
