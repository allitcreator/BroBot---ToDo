import asyncio
import sys
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import config
from api import routes as api_routes
from db import storage
from handlers import messages, commands, callbacks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def _reminder_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        try:
            due = await storage.get_due_reminders()
            for reminder in due:
                try:
                    await bot.send_message(reminder["chat_id"], reminder["text"])
                    await storage.delete_reminder(reminder["id"])
                except Exception as e:
                    logging.error(f"Reminder send error (id={reminder['id']}): {e}")
        except Exception as e:
            logging.error(f"Reminder loop error: {e}")


async def on_startup(bot: Bot):
    await storage.init_db()
    asyncio.create_task(_reminder_loop(bot))
    await bot.set_my_commands([
        BotCommand(command="todotoday", description="Задачи на сегодня"),
        BotCommand(command="tomorrow", description="Задачи на завтра"),
        BotCommand(command="todoall", description="Все открытые задачи"),
        BotCommand(command="overdue", description="Просроченные задачи"),
        BotCommand(command="today2tomorrow", description="Перенести сегодняшние на завтра"),
        BotCommand(command="overdue2today", description="Перенести просроченные на сегодня"),
        BotCommand(command="scheduled", description="Напоминания и календарь"),
        BotCommand(command="projects", description="Проекты"),
        BotCommand(command="app", description="Мини-апп: входящие"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="settings", description="Настройки"),
    ])
    if config.MINIAPP_URL:
        await bot.set_chat_menu_button(
            chat_id=config.TELEGRAM_USER_ID,
            menu_button=MenuButtonWebApp(text="Входящие", web_app=WebAppInfo(url=config.MINIAPP_URL)),
        )
    webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url, secret_token=config.WEBHOOK_SECRET or None)
    logging.info(f"Webhook set: {webhook_url}")


async def on_shutdown(bot: Bot):
    from services import ms_todo, google_calendar
    await ms_todo.close()
    await google_calendar.close()
    await storage.close_db()
    await bot.delete_webhook()


def run_webhook():
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(messages.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    handler = SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=config.WEBHOOK_SECRET or None,
    )
    handler.register(app, path="/")
    # API мини-аппа — в том же приложении: второй процесс на одном ядре
    # vps4 не нужен. Наружу его отдаёт nginx поддомена мини-аппа.
    api_routes.setup(app)
    setup_application(app, dp, bot=bot)

    logging.info(f"Starting webhook on {config.WEBAPP_HOST}:{config.WEBAPP_PORT}")
    web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)


async def run_polling():
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
        BotCommand(command="tomorrow", description="Задачи на завтра"),
        BotCommand(command="todoall", description="Все открытые задачи"),
        BotCommand(command="overdue", description="Просроченные задачи"),
        BotCommand(command="today2tomorrow", description="Перенести сегодняшние на завтра"),
        BotCommand(command="overdue2today", description="Перенести просроченные на сегодня"),
        BotCommand(command="scheduled", description="Напоминания и календарь"),
        BotCommand(command="projects", description="Проекты"),
        BotCommand(command="app", description="Мини-апп: входящие"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="settings", description="Настройки"),
    ])

    logging.info("Bot started (polling)")
    asyncio.create_task(_reminder_loop(bot))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        from services import ms_todo, google_calendar
        await ms_todo.close()
        await google_calendar.close()
        await storage.close_db()
        await bot.session.close()


if __name__ == "__main__":
    problems = config.config_problems()
    if problems:
        sys.exit("Бот не запущен, проблемы с .env:\n- " + "\n- ".join(problems))
    if config.USE_WEBHOOK:
        run_webhook()
    else:
        asyncio.run(run_polling())
