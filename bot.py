import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import config
from db import storage
from handlers import messages, commands, callbacks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def on_startup(bot: Bot):
    webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set: {webhook_url}")


async def on_shutdown(bot: Bot):
    from services import ms_todo, google_calendar
    await ms_todo.close()
    await google_calendar.close()
    await storage.close_db()
    await bot.delete_webhook()


async def main():
    await storage.init_db()

    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(messages.router)

    await bot.set_my_commands([
        BotCommand(command="todotoday", description="Задачи на сегодня"),
        BotCommand(command="todoall", description="Все открытые задачи"),
        BotCommand(command="overdue", description="Просроченные задачи"),
        BotCommand(command="week", description="События на неделю"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="settings", description="Настройки"),
    ])

    if config.USE_WEBHOOK:
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

        app = web.Application()
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=config.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        logging.info(f"Starting webhook on {config.WEBAPP_HOST}:{config.WEBAPP_PORT}")
        web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)
    else:
        logging.info("Bot started (polling)")
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        finally:
            from services import ms_todo, google_calendar
            await ms_todo.close()
            await google_calendar.close()
            await storage.close_db()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
