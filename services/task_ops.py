"""Действия над задачей, общие для чата и мини-аппа."""
import logging

from db import storage
from services import google_calendar, ms_todo

logger = logging.getLogger(__name__)


async def cleanup_task_extras(task_id: str):
    """Удаляет Telegram-напоминание и событие Google Calendar, связанные с задачей.

    Каждый шаг независим: сбой одного не должен оставлять остальные хвосты.
    """
    try:
        await storage.delete_telegram_reminder_by_task(task_id)
    except Exception:
        logger.warning("cleanup: telegram reminder for %s", task_id, exc_info=True)
    try:
        await ms_todo.remove_reminder(task_id)
    except Exception:
        logger.warning("cleanup: ms reminder for %s", task_id, exc_info=True)
    event_id = await storage.get_calendar_link(task_id)
    if event_id:
        try:
            await google_calendar.delete_event(event_id)
        except Exception:
            logger.warning("cleanup: calendar event %s for %s", event_id, task_id, exc_info=True)
        await storage.delete_calendar_link(task_id)
