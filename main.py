import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError

from config import (
    BOT_TOKEN,
    LOG_LEVEL,
    POLLING_RETRY_BASE_SECONDS,
    POLLING_RETRY_MAX_SECONDS,
)
from database import DB, init_db
from handlers import auth, common, mailing, scanner, start, tariff
from utils.logging_setup import configure_logging
from utils.sqlite_fsm import SQLiteStorage


logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    storage = SQLiteStorage(DB)
    dp = Dispatcher(storage=storage)

    dp.include_router(common.router)
    dp.include_router(start.router)
    dp.include_router(auth.router)
    dp.include_router(tariff.router)
    dp.include_router(scanner.router)
    dp.include_router(mailing.router)
    return dp


async def run_polling(dp: Dispatcher, bot: Bot) -> None:
    logger.info("Bot is starting")

    await dp.start_polling(bot)


async def run_polling_with_backoff() -> None:
    retry_delay = max(POLLING_RETRY_BASE_SECONDS, 1)
    bot = Bot(token=BOT_TOKEN)
    dp = create_dispatcher()

    try:
        while True:
            try:
                await run_polling(dp, bot)
                logger.info("Polling finished normally")
                return
            except TelegramNetworkError as exc:
                logger.warning(
                    "Polling stopped because of Telegram network error. Retrying in %s seconds: %s",
                    retry_delay,
                    exc,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max(POLLING_RETRY_MAX_SECONDS, 1))
            except Exception:
                logger.exception(
                    "Polling crashed unexpectedly. Retrying in %s seconds",
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max(POLLING_RETRY_MAX_SECONDS, 1))
    finally:
        await dp.storage.close()
        await bot.session.close()


async def main():
    configure_logging(LOG_LEVEL)
    await init_db()
    await run_polling_with_backoff()


if __name__ == "__main__":
    asyncio.run(main())
