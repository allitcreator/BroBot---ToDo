from datetime import date


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


def format_due_date(due_datetime: dict | None) -> str:
    if not due_datetime:
        return "без даты"
    try:
        dt_str = due_datetime.get("dateTime", "")[:10]
        d = date.fromisoformat(dt_str)
        return d.strftime("%d.%m.%Y")
    except Exception:
        return "?"
