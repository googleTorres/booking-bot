import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

log = logging.getLogger(__name__)


async def send_safe(bot: Bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as e:
        log.warning("Could not message %s: %s", chat_id, e)
        return False


async def notify_owners(bot: Bot, owner_ids: list[int], text: str) -> None:
    for owner_id in owner_ids:
        await send_safe(bot, owner_id, text)
