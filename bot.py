import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import config
from api import routes as api_routes
from db import storage
from handlers import messages, commands, callbacks
from handlers.keyboards import reminder_fire_kb
from services import reminder_sync, rituals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


_ritual_task: asyncio.Task | None = None


async def _ritual_tick(bot: Bot):
    try:
        # Graph или Telegram могут висеть до таймаута клиента; дольше минуты
        # проход не держим — следующий тик повторит.
        await asyncio.wait_for(rituals.tick(bot), 60)
    except Exception:
        logging.exception("Ritual tick error")


REMINDER_MAX_ATTEMPTS = 5      # попыток отправки (по одной за проход цикла, 30 с)
TRANSIENT_GRACE = timedelta(hours=2)  # временные сбои не считаются попытками первые 2 ч от fire_at
SYNC_INTERVAL = 300            # синк напоминаний MS Todo, секунд
_sync_task: asyncio.Task | None = None
_last_sync = 0.0
_retry_after: dict[int, float] = {}   # id напоминания -> monotonic, раньше которого не пробуем (flood control)
_sent_unmarked: set[int] = set()      # отправлено, но пометка в БД не удалась: не слать повторно


async def _sync_tick():
    try:
        # Graph может висеть до таймаута клиента; дольше минуты проход не держим.
        await asyncio.wait_for(reminder_sync.sync(), 60)
    except Exception:
        logging.exception("Reminder sync error")


def _parse_fire_at(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _on_send_error(reminder: dict, e: Exception):
    """Постоянная ошибка (бот заблокирован, чата нет) — снимаем сразу; временная
    (сеть, таймаут, flood control) — ждём, не считая попыткой, пока не прошло
    2 ч от fire_at; прочее — потолок REMINDER_MAX_ATTEMPTS."""
    rid = reminder["id"]
    if isinstance(e, (TelegramForbiddenError, TelegramBadRequest)):
        logging.error(f"Reminder dropped, permanent Telegram error (id={rid}): {e}")
        await storage.give_up_reminder(rid)
        return
    if isinstance(e, TelegramRetryAfter):
        _retry_after[rid] = time.monotonic() + e.retry_after
    fire = _parse_fire_at(reminder.get("fire_at", ""))
    transient = isinstance(e, (TelegramRetryAfter, TelegramNetworkError, TelegramServerError, asyncio.TimeoutError))
    if transient and fire and datetime.now(timezone.utc) - fire < TRANSIENT_GRACE:
        logging.warning(f"Reminder send transient error (id={rid}): {e}")
        return
    attempts = await storage.bump_reminder_attempts(rid)
    if attempts >= REMINDER_MAX_ATTEMPTS:
        logging.error(f"Reminder dropped after {attempts} attempts (id={rid}): {e}")
        await storage.give_up_reminder(rid)
    else:
        logging.error(f"Reminder send error (id={rid}, attempt {attempts}): {e}")


async def deliver_due(bot: Bot):
    """Отправляет созревшие напоминания. Отправка, пометка и удаление — раздельно:
    сбой пометки или удаления не даёт повторной отправки, один сбой не обрывает проход."""
    for rid in list(_sent_unmarked):   # отправленное, но не помеченное — дометить
        try:
            await storage.mark_reminder_sent(rid)
            _sent_unmarked.discard(rid)
        except Exception as e:
            logging.error(f"Reminder mark error (id={rid}): {e}")
    try:
        await storage.delete_sent_reminders()
    except Exception as e:
        logging.error(f"Reminder cleanup error: {e}")

    for reminder in await storage.get_due_reminders():
        rid = reminder["id"]
        if rid in _sent_unmarked or _retry_after.get(rid, 0) > time.monotonic():
            continue
        _retry_after.pop(rid, None)
        try:
            # Пара уже доставлена (гонка check-then-insert в синке) — не дублируем.
            if reminder["task_id"] and await storage.reminder_pair_logged(reminder["task_id"], reminder["fire_at"]):
                await storage.delete_reminder(rid)
                continue
            markup = None
            if reminder["task_id"]:
                markup = reminder_fire_kb(await storage.register_task_id(reminder["task_id"]))
            await bot.send_message(reminder["chat_id"], reminder["text"], reply_markup=markup)
        except Exception as e:
            try:
                await _on_send_error(reminder, e)
            except Exception as e2:
                logging.error(f"Reminder error handling failed (id={rid}): {e2}")
            continue
        try:
            await storage.mark_reminder_sent(rid)
        except Exception as e:
            _sent_unmarked.add(rid)
            logging.error(f"Reminder mark error (id={rid}): {e}")
            continue
        try:
            await storage.delete_reminder(rid)
        except Exception as e:
            logging.error(f"Reminder delete error (id={rid}): {e}")  # помечена — доудалится позже


async def _reminder_loop(bot: Bot):
    global _ritual_task, _sync_task, _last_sync
    while True:
        await asyncio.sleep(30)
        # Сводки и синк — отдельными задачами: зависший Graph не задерживает
        # напоминания. Новый проход — только когда прошлый закончился.
        if _ritual_task is None or _ritual_task.done():
            _ritual_task = asyncio.create_task(_ritual_tick(bot))
        if (_sync_task is None or _sync_task.done()) and time.monotonic() - _last_sync >= SYNC_INTERVAL:
            _last_sync = time.monotonic()
            _sync_task = asyncio.create_task(_sync_tick())
        try:
            await deliver_due(bot)
        except Exception as e:
            logging.error(f"Reminder loop error: {e}")


_COMMANDS = [
    BotCommand(command="todotoday", description="Задачи на сегодня"),
    BotCommand(command="tomorrow", description="Задачи на завтра"),
    BotCommand(command="todoall", description="Все открытые задачи"),
    BotCommand(command="overdue", description="Просроченные задачи"),
    BotCommand(command="today2tomorrow", description="Перенести сегодняшние на завтра"),
    BotCommand(command="overdue2today", description="Перенести просроченные на сегодня"),
    BotCommand(command="scheduled", description="Напоминания и календарь"),
    BotCommand(command="projects", description="Проекты"),
    BotCommand(command="app", description="Мини-апп: задачи и разбор"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="settings", description="Настройки"),
]


async def _with_retries(call, what: str, attempts: int = 5, base_delay: float = 2.0):
    """Повторяет сетевой вызов к Telegram с нарастающей паузой (2, 4, 8, 16 с)."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as e:
            if attempt == attempts:
                raise
            logging.warning(f"{what}: попытка {attempt} не удалась ({e}), повторяю")
            await asyncio.sleep(base_delay * 2 ** (attempt - 1))


async def on_startup(bot: Bot):
    await storage.init_db()
    asyncio.create_task(_reminder_loop(bot))

    # Команды и кнопка меню — удобство: если Telegram недоступен, бот всё
    # равно должен подняться и принимать апдейты.
    try:
        await _with_retries(lambda: bot.set_my_commands(_COMMANDS), "set_my_commands")
    except Exception:
        logging.error("Не удалось выставить команды бота", exc_info=True)
    if config.MINIAPP_URL:
        try:
            await _with_retries(lambda: bot.set_chat_menu_button(
                chat_id=config.TELEGRAM_USER_ID,
                menu_button=MenuButtonWebApp(text="Задачи", web_app=WebAppInfo(url=config.MINIAPP_URL)),
            ), "set_chat_menu_button")
        except Exception:
            logging.error("Не удалось выставить кнопку мини-аппа", exc_info=True)

    # Без вебхука бот глух. Если он так и не встал — выходим с ошибкой, чтобы
    # docker перезапустил контейнер: исключение из on_startup оставляло
    # процесс живым, но не слушающим порт (2026-10-26).
    webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
    try:
        await _with_retries(
            lambda: bot.set_webhook(webhook_url, secret_token=config.WEBHOOK_SECRET or None),
            "set_webhook",
        )
    except Exception:
        logging.critical("Вебхук не установлен, выходим для перезапуска", exc_info=True)
        logging.shutdown()
        os._exit(1)
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
    app["bot"] = bot  # уведомление «📖 Новое» из API материалов
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

    await bot.set_my_commands(_COMMANDS)

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
