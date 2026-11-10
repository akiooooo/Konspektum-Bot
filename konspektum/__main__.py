import argparse
import asyncio
import logging
from contextlib import suppress
from dataclasses import replace

from aiohttp import web

from .bot import make_bot, setup_commands
from .config import Config
from .store import Store
from .web import create_app
from .delivery import status_worker
from .security import SecretFormatter
from .polling import ConflictGuard


async def main(demo=False,web_only=False,port=None,host=None):
    config = Config.from_env(demo)
    if port is not None:
        if not 1<=port<=65535:
            raise ValueError('PORT должен быть от 1 до 65535.')
        config=replace(config,port=port)
    if host is not None:
        config=replace(config,host=host)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SecretFormatter(config.token))
    store = Store(config)
    await store.initialize()
    bot, dispatcher = (None, None) if demo else make_bot(config, store)
    if bot:
        try:
            me=await asyncio.wait_for(bot.get_me(),timeout=15)
            config=replace(config,bot_username=me.username)
        except BaseException:
            await bot.session.close()
            raise
    runner = web.AppRunner(create_app(config, store, bot), access_log=None)
    await runner.setup()
    worker=None
    try:
        await web.TCPSite(runner, config.host, config.port).start()
        logging.info('Конспектум: http://%s:%s · магазин запущен', config.host, config.port)
        if demo or web_only:
            await asyncio.Event().wait()
        else:
            webhook=await bot.get_webhook_info()
            if webhook.url:
                raise ValueError('У бота установлен webhook. Удалите его перед запуском long polling; автоматическое удаление отключено, чтобы не прервать другой сервер.')
            await setup_commands(bot)
            worker=asyncio.create_task(status_worker(bot,store))
            guard=ConflictGuard(dispatcher)
            bot.session.middleware(guard)
            await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False)
            if guard.stop_task:
                await guard.stop_task
            if guard.conflict:
                raise ValueError('Telegram обнаружил другую работающую копию этого бота. Эта копия остановлена, чтобы вход и заказы не попадали в разные базы. Оставьте один запуск бота вместе с его сайтом.')
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await runner.cleanup()
        if bot:
            await bot.session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Конспектум · Telegram-магазин')
    parser.add_argument('--preview','--demo',dest='demo',action='store_true',help='Локальный просмотр без подключения Telegram')
    parser.add_argument('--web-only',action='store_true',help='Веб-сервер без чтения входящих Telegram-сообщений')
    parser.add_argument('--port',type=int,help='Порт локального сервера')
    parser.add_argument('--host',help='Адрес прослушивания, например 127.0.0.1')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # HTTP-клиент не должен писать URL с токеном бота в журнал.
    logging.getLogger('aiohttp.access').disabled = True
    logging.getLogger('aiogram.event').setLevel(logging.WARNING)
    try:
        asyncio.run(main(args.demo,args.web_only,args.port,args.host))
    except (KeyboardInterrupt, SystemExit):
        pass
    except ValueError as exc:
        raise SystemExit(str(exc))
