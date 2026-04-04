from datetime import date, datetime
from zoneinfo import ZoneInfo
import config


async def ask_reminder_if_needed(bot, chat_id: int, user_id: int, task_id: str, task_title: str, due_date: str, due_time: str | None):
    """Если задача имеет время — спрашивает про напоминание."""
    if not due_time:
        return
    from handlers.keyboards import reminder_ask_kb
    from db import storage
    remind_msg = await bot.send_message(
        chat_id,
        f"⏰ Напомнить в {due_time}?",
        reply_markup=reminder_ask_kb(),
    )
    await storage.set_state(user_id, "reminder_pending", {
        "task_id": task_id,
        "task_title": task_title,
        "due_date": due_date,
        "due_time": due_time,
        "remind_q_msg_id": remind_msg.message_id,
        "chat_id": chat_id,
    })


def build_task_text(title: str, date_str: str, has_reminder: bool, in_calendar: bool) -> str:
    """Строит текст сообщения задачи с маркерами."""
    prefix = ""
    if has_reminder:
        prefix += "⏰"
    if in_calendar:
        prefix += "🗓"
    if prefix:
        prefix += " "
    return f"{prefix}{title} — {date_str}"


def rebuild_task_text(current_text: str, has_reminder: bool, in_calendar: bool) -> str:
    """Обновляет маркеры в уже существующем тексте задачи."""
    clean = current_text.lstrip("⏰🗓 ")
    prefix = ""
    if has_reminder:
        prefix += "⏰"
    if in_calendar:
        prefix += "🗓"
    if prefix:
        prefix += " "
    return prefix + clean


def format_task_preview(task: dict) -> str:
    lines = [f"📌 {task['title']}"]

    due = task.get("due_date")
    due_time = task.get("due_time")
    if due:
        try:
            d = date.fromisoformat(due)
            date_str = d.strftime("%d.%m.%Y")
        except ValueError:
            date_str = due
        if due_time:
            lines.append(f"📅 {date_str} в {due_time}")
        else:
            lines.append(f"📅 {date_str}")

    desc = task.get("description")
    if desc:
        # Показываем первые 100 символов описания
        short = desc[:100] + ("..." if len(desc) > 100 else "")
        lines.append(f"📝 {short}")

    subtasks = task.get("subtasks")
    if subtasks:
        lines.append("☑️ Подзадачи:")
        for s in subtasks:
            lines.append(f"  • {s}")

    return "\n".join(lines)


def format_fire_at(fire_at_utc: str) -> str:
    """Конвертирует fire_at из UTC в локальное время и форматирует."""
    dt_utc = datetime.fromisoformat(fire_at_utc).replace(tzinfo=ZoneInfo("UTC"))
    local_dt = dt_utc.astimezone(ZoneInfo(config.USER_TIMEZONE))
    return local_dt.strftime("%d.%m.%Y %H:%M")


def format_event_info(event: dict) -> str:
    """Форматирует время и длительность события Google Calendar."""
    start_str = event.get("start", {}).get("dateTime", "")
    end_str = event.get("end", {}).get("dateTime", "")
    if not start_str or not end_str:
        return "?"
    start_dt = datetime.fromisoformat(start_str)
    end_dt = datetime.fromisoformat(end_str)
    local_tz = ZoneInfo(config.USER_TIMEZONE)
    local_start = start_dt.astimezone(local_tz)
    duration_min = int((end_dt - start_dt).total_seconds() / 60)
    time_str = local_start.strftime("%d.%m.%Y %H:%M")
    if duration_min >= 60 and duration_min % 60 == 0:
        dur_str = f"{duration_min // 60} ч"
    elif duration_min >= 60:
        dur_str = f"{duration_min // 60} ч {duration_min % 60} мин"
    else:
        dur_str = f"{duration_min} мин"
    return f"{time_str}, {dur_str}"


def build_scheduled_task_text(
    title: str, date_str: str, has_reminder: bool, in_calendar: bool,
    reminder_info: dict | None, event_info: dict | None,
) -> str:
    """Расширенный текст задачи для /scheduled."""
    base = build_task_text(title, date_str, has_reminder, in_calendar)
    details = []
    if reminder_info and reminder_info.get("fire_at"):
        details.append(f"⏰ Напоминание: {format_fire_at(reminder_info['fire_at'])}")
    if event_info:
        details.append(f"🗓 Событие: {format_event_info(event_info)}")
    if details:
        return base + "\n\n" + "\n".join(details)
    return base


def format_due_date(due_datetime: dict | None) -> str:
    if not due_datetime:
        return "без даты"
    try:
        dt_str = due_datetime.get("dateTime", "")[:10]
        d = date.fromisoformat(dt_str)
        return d.strftime("%d.%m.%Y")
    except Exception:
        return "?"
