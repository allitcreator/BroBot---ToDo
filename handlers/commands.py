from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import config
from db import storage
from services import ms_todo, google_calendar
from handlers.keyboards import task_actions_kb, overdue_task_kb, settings_kb
from handlers.utils import build_task_text
from db.storage import register_task_id, save_task_header

router = Router()

user_filter = F.from_user.func(lambda u: u.id == config.TELEGRAM_USER_ID)


async def _send_task_list(message: Message, tasks: list[dict], header: str):
    """Универсальный вывод списка задач с маркерами напоминаний и календаря."""
    task_ids = [t["id"] for t in tasks]
    tg_reminder_ids = await storage.get_task_ids_with_any_reminder(task_ids)
    calendar_ids = await storage.get_task_ids_in_calendar(task_ids)

    header_msg = await message.answer(header)
    for task in tasks:
        tid = task["id"]
        has_reminder = task.get("isReminderOn", False) or tid in tg_reminder_ids
        in_calendar = tid in calendar_ids
        key = await register_task_id(tid)
        await save_task_header(key, message.chat.id, header_msg.message_id)
        text = build_task_text(
            task["title"],
            ms_todo.format_due_date_from_task(task),
            has_reminder,
            in_calendar,
        )
        await message.answer(text, reply_markup=task_actions_kb(key))


@router.message(Command("start"), user_filter)
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я ToDo бот.\n\n"
        "Просто напиши задачу, и я добавлю её в Microsoft Todo.\n\n"
        "Команды:\n"
        "/todotoday — задачи на сегодня\n"
        "/tomorrow — задачи на завтра\n"
        "/todoall — все открытые задачи\n"
        "/overdue — просроченные\n"
        "/reminders — задачи с напоминаниями\n"
        "/incalendar — задачи в Google Calendar\n"
        "/stats — статистика\n"
        "/settings — настройки"
    )


@router.message(Command("skip"), user_filter)
async def cmd_skip(message: Message):
    user_id = message.from_user.id
    await storage.clear_state(user_id)
    await message.answer("Пропущено.")


@router.message(Command("todotoday"), user_filter)
async def cmd_todotoday(message: Message):
    try:
        tasks = await ms_todo.get_tasks_today()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return
    if not tasks:
        await message.answer("📭 Задач на сегодня нет.")
        return
    await _send_task_list(message, tasks, f"📋 Задачи на сегодня ({len(tasks)}):")


@router.message(Command("tomorrow"), user_filter)
async def cmd_tomorrow(message: Message):
    try:
        tasks = await ms_todo.get_tasks_tomorrow()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return
    if not tasks:
        await message.answer("📭 Задач на завтра нет.")
        return
    await _send_task_list(message, tasks, f"📋 Задачи на завтра ({len(tasks)}):")


@router.message(Command("todoall"), user_filter)
async def cmd_todoall(message: Message):
    try:
        tasks = await ms_todo.get_all_tasks()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return
    if not tasks:
        await message.answer("📭 Открытых задач нет.")
        return
    await _send_task_list(message, tasks, f"📋 Все открытые задачи ({len(tasks)}):")


@router.message(Command("overdue"), user_filter)
async def cmd_overdue(message: Message):
    try:
        tasks = await ms_todo.get_overdue_tasks()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return
    if not tasks:
        await message.answer("✅ Просроченных задач нет.")
        return
    await _send_task_list(message, tasks, f"⚠️ Просроченные задачи ({len(tasks)}):")


@router.message(Command("reminders"), user_filter)
async def cmd_reminders(message: Message):
    try:
        all_tasks = await ms_todo.get_all_tasks()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    tg_reminder_ids = await storage.get_all_telegram_reminder_task_ids()
    tasks = [t for t in all_tasks if t.get("isReminderOn", False) or t["id"] in tg_reminder_ids]

    if not tasks:
        await message.answer("📭 Задач с напоминаниями нет.")
        return
    await _send_task_list(message, tasks, f"⏰ Задачи с напоминаниями ({len(tasks)}):")


@router.message(Command("incalendar"), user_filter)
async def cmd_incalendar(message: Message):
    cal_task_ids = await storage.get_all_calendar_task_ids()
    if not cal_task_ids:
        await message.answer("📭 Нет задач добавленных в Google Calendar.")
        return

    tasks = []
    for tid in cal_task_ids:
        try:
            task = await ms_todo.get_task(tid)
            if task.get("status") != "completed":
                tasks.append(task)
        except Exception:
            pass

    if not tasks:
        await message.answer("📭 Нет задач добавленных в Google Calendar.")
        return
    await _send_task_list(message, tasks, f"🗓 В Google Calendar ({len(tasks)}):")


@router.message(Command("stats"), user_filter)
async def cmd_stats(message: Message):
    try:
        s = await ms_todo.get_stats()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    await message.answer(
        f"📊 Статистика\n\n"
        f"✅ Выполнено сегодня: {s['completed_today']}\n"
        f"🆕 Создано сегодня: {s['created_today']}\n"
        f"📋 Открытых задач: {s['open_tasks']}\n"
        f"⚠️ Просрочено: {s['overdue']}"
    )


@router.message(Command("settings"), user_filter)
async def cmd_settings(message: Message):
    user_id = message.from_user.id
    confirm_mode = await storage.get_confirm_mode(user_id)
    mode_labels = {"all": "Все задачи", "uncertain": "Только неуверенные", "off": "Отключено"}
    await message.answer(
        f"⚙️ Настройки\n\n"
        f"Режим подтверждения: {mode_labels.get(confirm_mode, confirm_mode)}\n\n"
        f"Выбери режим:",
        reply_markup=settings_kb(confirm_mode),
    )
