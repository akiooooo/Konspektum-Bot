import asyncio
import html
import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .store import STATUSES


async def send_reply(bot, store, order_id, actor_id, body, request_key):
    if bot is None:
        raise ValueError('Telegram временно недоступен. Сообщение не отправлено.')
    message, order, fresh = await store.prepare_reply(order_id, actor_id, body, request_key)
    if not fresh:
        return message
    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Ответить по заказу',callback_data=f'chat:{order_id}')]])
    try:
        sent=await asyncio.wait_for(bot.send_message(order['user_id'],
            f'<b>Конспектум · {html.escape(order["code"])}</b>\n\n{html.escape(message["body"])}',reply_markup=markup),timeout=12)
    except (TelegramForbiddenError,TelegramBadRequest):
        await store.finish_reply(message['id'],'failed')
        return message | {'state':'failed'}
    except (TelegramAPIError,TimeoutError):
        # При сетевом обрыве нельзя доказать, что Telegram не принял сообщение.
        await store.finish_reply(message['id'],'unknown')
        return message | {'state':'unknown'}
    except BaseException:
        await store.finish_reply(message['id'],'unknown')
        raise
    await store.finish_reply(message['id'],'sent',sent.message_id)
    return message | {'state':'sent','telegram_message_id':sent.message_id}


async def send_status(bot,store,event_id):
    if bot is None or event_id is None:
        return False
    event=await store.claim_status(event_id)
    if event is None:
        return False
    explanations={'new':'Заказ принят. Согласуем детали в личных сообщениях.',
        'discussing':'Уточняем материалы, сроки и стоимость.',
        'in_progress':'Мы приступили к вашему конспекту.',
        'done':'Ваш заказ готов. Детали получения обсудим в личных сообщениях.',
        'cancelled':'Заказ отменён. Если остались вопросы, напишите нам.'}
    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Открыть заказ',callback_data=f'order:{event["order_id"]}')]])
    try:
        await asyncio.wait_for(bot.send_message(event['user_id'],f'<b>Конспектум · {html.escape(event["code"])}</b>\n\n'
            f'Статус: <b>{STATUSES[event["status"]]}</b>\n{explanations[event["status"]]}',reply_markup=markup),timeout=8)
    except (TelegramForbiddenError,TelegramBadRequest):
        await store.finish_status(event_id,'blocked')
        return False
    except (TelegramAPIError,TimeoutError):
        await store.finish_status(event_id,'pending',min(600,15*2**min(event['attempts'],5)))
        return False
    except BaseException:
        await store.finish_status(event_id,'pending',30)
        raise
    await store.finish_status(event_id,'sent')
    return True


async def status_worker(bot,store):
    await store.recover_statuses()
    while True:
        try:
            for event_id in await store.pending_statuses():
                await send_status(bot,store,event_id)
        except Exception:
            logging.getLogger(__name__).error('Status notification queue is temporarily unavailable; retrying.')
        await asyncio.sleep(10)
