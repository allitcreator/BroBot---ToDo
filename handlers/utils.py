from datetime import date


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
