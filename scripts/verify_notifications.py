"""Explicitly send setup checks to the configured notification groups."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiogram import Bot
from konspektum.config import Config
from konspektum.store import Store
from konspektum.notifications import deliver_notice


async def main():
    cfg = Config.from_env()
    store = Store(cfg)
    await store.initialize()
    await store.update_settings({'notify_chat_id': cfg.notify_chat_id})
    async with Bot(cfg.token) as bot:
        result = await deliver_notice(bot, store, cfg,
            'Конспектум: проверка уведомлений о заказах. Группа Orders настроена. Это тест, заказ не создавался.')
        print('orders_delivery=' + str(result['ok']))
        if not result['ok']:
            raise RuntimeError(result['message'])
        message = await bot.send_message(cfg.log_chat_id,
            'Конспектум: проверка служебных логов. Группа Logs настроена. Уведомления о заказах направляются отдельно в Orders.')
        print(f'logs_delivery=True message_id={message.message_id}')
        print('stored_orders_chat=' + (await store.settings())['notify_chat_id'])


asyncio.run(main())
