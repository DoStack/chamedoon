from __future__ import annotations

import asyncio
import logging

from config import BOT_TOKEN, MINI_APP_URL, mini_app_https_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("koolbar.bot")


async def _idle() -> None:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Bot is idling.")
    while True:
        await asyncio.sleep(3600)


async def run_bot() -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

    from flows import router as flow_router
    from handlers import router

    bot = Bot(BOT_TOKEN)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    dispatcher.include_router(flow_router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Open Koolbar"),
            BotCommand(command="send", description="I need to send"),
            BotCommand(command="carry", description="I can carry"),
            BotCommand(command="requests", description="My requests"),
            BotCommand(command="matches", description="My matches"),
            BotCommand(command="cancel", description="Cancel current request"),
        ]
    )
    menu_url = mini_app_https_url()
    if menu_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Open Koolbar", web_app=WebAppInfo(url=menu_url))
        )
    elif MINI_APP_URL:
        logger.info("Mini App URL is not HTTPS (%s). Using t.me deep links.", MINI_APP_URL)

    logger.info("Koolbar bot polling started.")
    await dispatcher.start_polling(bot)


def main() -> None:
    if not BOT_TOKEN:
        asyncio.run(_idle())
        return
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
