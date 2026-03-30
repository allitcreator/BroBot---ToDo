from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import config
from db import storage
from services import ms_todo, google_calendar
from handlers.keyboards import task_actions_kb, overdue_task_kb, settings_kb
from db.storage import register_task_id

router = Router()

user_filter = F.from_user.func(lambda u: u.id == config.TELEGRAM_USER_ID)


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
        "/week — события на неделю\n"
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

    await message.answer(f"📋 Задачи на сегодня ({len(tasks)}):")
    for task in tasks:
        key = await register_task_id(task["id"])
        await message.answer(task["title"], reply_markup=task_actions_kb(key))


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

    await message.answer(f"📋 Задачи на завтра ({len(tasks)}):")
    for task in tasks:
        key = await register_task_id(task["id"])
        await message.answer(task["title"], reply_markup=task_actions_kb(key))


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

    await message.answer(f"📋 Все открытые задачи ({len(tasks)}):")
    for task in tasks:
        key = await register_task_id(task["id"])
        await message.answer(
            f"{task['title']} — {ms_todo.format_due_date_from_task(task)}",
            reply_markup=task_actions_kb(key),
        )


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

    await message.answer(f"⚠️ Просроченные задачи ({len(tasks)}):")
    for task in tasks:
        key = await register_task_id(task["id"])
        await message.answer(
            f"{task['title']} — {ms_todo.format_due_date_from_task(task)}",
            reply_markup=overdue_task_kb(key),
        )


WEEKDAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


@router.message(Command("week"), user_filter)
async def cmd_week(message: Message):
    try:
        events = await google_calendar.get_events_week()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    if not events:
        await message.answer("📭 Событий на ближайшую неделю нет.")
        return

    tz = ZoneInfo(config.USER_TIMEZONE)

    # Группируем по дням
    days: dict[str, list[str]] = {}
    for ev in events:
        start = ev.get("start", {})
        # Целодневные события
        if "date" in start:
            day_str = start["date"]
            time_str = "весь день"
        else:
            dt = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
            day_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")

        summary = ev.get("summary", "Без названия")
        days.setdefault(day_str, []).append(f"  {time_str} — {summary}")

    lines = ["📅 События на неделю:\n"]
    for day_str in sorted(days):
        d = datetime.strptime(day_str, "%Y-%m-%d").date()
        weekday = WEEKDAYS_RU[d.weekday()]
        lines.append(f"**{weekday}, {d.strftime('%d.%m')}**")
        lines.extend(days[day_str])
        lines.append("")

    await message.answer("\n".join(lines))


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
