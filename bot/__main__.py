import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

from bot.config import get_settings
from bot.db.seed import seed_demo
from bot.db.session import create_engine, create_sessionmaker, init_db
from bot.handlers import setup_routers
from bot.middlewares.db import DbSessionMiddleware
from bot.services.scheduler import scheduler_loop

CLIENT_COMMANDS = [BotCommand(command="start", description="Book an appointment")]
OWNER_COMMANDS = CLIENT_COMMANDS + [
    BotCommand(command="today", description="Today's schedule"),
    BotCommand(command="tomorrow", description="Tomorrow's schedule"),
    BotCommand(command="services", description="Manage services"),
    BotCommand(command="addservice", description="Add a service"),
    BotCommand(command="staff", description="Manage specialists"),
    BotCommand(command="rating", description="Average rating"),
]


def utc_now() -> datetime:
    return datetime.now(UTC)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    engine = create_engine(settings.database_url)
    await init_db(engine)
    sessionmaker = create_sessionmaker(engine)
    if settings.seed_demo:
        async with sessionmaker() as session:
            if await seed_demo(session):
                logging.info("Seeded demo services and staff")

    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(settings=settings, clock=utc_now)
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.include_router(setup_routers())

    await bot.set_my_commands(CLIENT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    for owner_id in settings.owner_ids:
        with suppress(TelegramAPIError):  # owner hasn't started the bot yet
            await bot.set_my_commands(OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=owner_id))

    if not settings.payments_enabled:
        logging.warning("PAYMENT_PROVIDER_TOKEN is not set — bookings are confirmed without a deposit")

    scheduler = asyncio.create_task(scheduler_loop(bot, sessionmaker, settings, utc_now))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
