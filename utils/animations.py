import asyncio
from aiogram.enums import ChatAction

async def typing_animation(bot, chat_id, seconds=2):
    for _ in range(seconds):
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(1)
