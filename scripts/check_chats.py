"""Read-only Telegram destination diagnostics; never print the bot token."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiogram import Bot
from konspektum.config import Config


async def main():
    config = Config.from_env()
    async with Bot(config.token) as bot:
        me = await bot.get_me()
        print('bot=@' + me.username)
        for number in sys.argv[1:]:
            if not number.isdigit():
                raise ValueError('Pass numeric group identifiers without a prefix')
            for candidate in (number, '-' + number, '-100' + number):
                try:
                    chat = await bot.get_chat(candidate, request_timeout=15)
                    member = await bot.get_chat_member(chat.id, me.id)
                    print(f'{candidate}: id={chat.id} type={chat.type} title={chat.title!r} bot_status={member.status}')
                except Exception as exc:
                    print(f'{candidate}: {type(exc).__name__}: {str(exc).replace(config.token, "[REDACTED]")}')


asyncio.run(main())
