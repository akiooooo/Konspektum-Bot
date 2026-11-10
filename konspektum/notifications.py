import asyncio

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat


def notification_error(exc):
    if isinstance(exc,TelegramForbiddenError):
        return 'Бот не может писать в чат: добавьте его в группу и разрешите отправку сообщений. Для канала нужны права администратора на публикацию.'
    if isinstance(exc,TelegramBadRequest):
        message=exc.message.lower()
        if 'chat not found' in message:
            return 'Чат не найден. Проверьте полный числовой ID: у групп он отрицательный, у супергрупп обычно начинается с -100. Бот должен быть участником именно этого чата.'
        if 'not enough rights' in message or 'chat_write_forbidden' in message:
            return 'Недостаточно прав: разрешите боту отправлять сообщения в этот чат.'
    return 'Telegram не подтвердил отправку. Проверьте соединение и повторите проверку.'


async def deliver_notice(bot,store,config,text,private_markup=None,group_markup=None):
    settings=await store.settings()
    primary=[settings['notify_chat_id']] if settings['notify_chat_id'] else [str(x) for x in config.admins]

    async def send(destination):
        destination=str(destination)
        markup=private_markup if int(destination)>0 else group_markup
        try:
            try:
                await asyncio.wait_for(bot.send_message(destination,text,reply_markup=markup),timeout=10)
            except TelegramMigrateToChat as exc:
                replacement=str(exc.migrate_to_chat_id)
                if (await store.settings())['notify_chat_id']==destination:
                    await store.update_settings({'notify_chat_id':replacement})
                destination=replacement
                await asyncio.wait_for(bot.send_message(destination,text,reply_markup=markup),timeout=10)
            return destination,None
        except (TelegramAPIError,TimeoutError) as exc:
            return None,notification_error(exc)

    results=await asyncio.gather(*(send(x) for x in primary))
    delivered=[destination for destination,error in results if destination]
    errors=[error for destination,error in results if error]
    ok=bool(delivered)
    fallback=False
    if not delivered and settings['notify_chat_id']:
        backups=await asyncio.gather(*(send(x) for x in config.admins if str(x) not in primary))
        delivered=[destination for destination,error in backups if destination]
        fallback=bool(delivered)
    return {'ok':ok,'delivered':bool(delivered),'fallback':fallback,'delivered_to':delivered,
            'message':'✓ Проверка доставлена в настроенный чат.' if ok else (errors[0] if errors else 'Получатели не настроены.')+(' Копия отправлена администраторам в бот.' if fallback else '')}
