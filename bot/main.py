from __future__ import annotations

import asyncio
import logging

from config import BOT_TOKEN, MINI_APP_URL, mini_app_https_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chamedoon.bot")


async def _idle() -> None:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Bot is idling.")
    while True:
        await asyncio.sleep(3600)


async def run_bot() -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

    from handlers import router
    from keyboards import COMMANDS, MENU_BUTTON

    bot = Bot(BOT_TOKEN)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    await bot.set_my_commands([BotCommand(command=name, description=text) for name, text in COMMANDS])
    # The menu button opens the courier Mini App page (/app/courier/), not the housing sections.
    menu_url = mini_app_https_url("courier")
    if menu_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=MENU_BUTTON, web_app=WebAppInfo(url=menu_url)))
    elif MINI_APP_URL:
        logger.info("Mini App URL is not HTTPS (%s). Using t.me deep links.", MINI_APP_URL)

    logger.info("Chamedoon courier bot polling started.")
    await dispatcher.start_polling(bot)


def main() -> None:
    if not BOT_TOKEN:
        asyncio.run(_idle())
        return
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
