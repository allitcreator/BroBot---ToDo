"""Действия над задачей, общие для чата и мини-аппа."""
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

import config
from db import storage
from services import google_calendar, ms_todo

logger = logging.getLogger(__name__)

# Перенос «в то же время»: время задачи сохраняется, меняется только день.
KEEP = "keep"


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


def _utc(dt_str: str) -> datetime:
    """Время из БД или Graph (UTC без зоны, возможно с дробью) — aware UTC."""
    return datetime.fromisoformat(dt_str.split(".")[0].rstrip("Z")).replace(tzinfo=timezone.utc)


def _utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


async def _extras(task_id: str, task: dict | None) -> dict:
    """Всё, где у задачи живёт время: событие, напоминания бота и MS To Do.

    `anchor` — время задачи: начало события, иначе первое напоминание.
    Событие, которое Google уже не находит, отвязывается.
    """
    out = {"event_id": None, "start": None, "end": None, "event_error": False, "tg": [], "ms": None}

    event_id = await storage.get_calendar_link(task_id)
    if event_id:
        try:
            event = await google_calendar.get_event(event_id)
            start = (event.get("start") or {}).get("dateTime")
            end = (event.get("end") or {}).get("dateTime")
            if start and end:
                out.update(event_id=event_id, start=datetime.fromisoformat(start), end=datetime.fromisoformat(end))
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 410):
                await storage.delete_calendar_link(task_id)
            else:
                logger.warning("reschedule: read event %s", event_id, exc_info=True)
                out.update(event_id=event_id, event_error=True)
        except Exception:
            logger.warning("reschedule: read event %s", event_id, exc_info=True)
            out.update(event_id=event_id, event_error=True)

    out["tg"] = [(rid, _utc(fire_at)) for rid, fire_at in await storage.get_task_reminders(task_id)]

    if task is None:
        # Задачу целиком читаем, только если у неё вообще есть время:
        # напоминание MS ставится ботом вместе с напоминанием бота.
        # Без задачи на руках не видно лишь напоминаний, поставленных в
        # самом To Do, — мини-апп передаёт задачу и их тоже двигает.
        task = await ms_todo.get_task(task_id) if (event_id or out["tg"]) else {}
    reminder = task.get("reminderDateTime") or {}
    if task.get("isReminderOn") and reminder.get("dateTime"):
        out["ms"] = _utc(reminder["dateTime"])

    candidates = [out["start"]] + [fire for _, fire in out["tg"]] + [out["ms"]]
    out["anchor"] = next((c for c in candidates if c is not None), None)
    return out


async def task_time(task_id: str, task: dict | None = None) -> str | None:
    """Время задачи «ЧЧ:ММ» по местному времени или None, если его нет."""
    extras = await _extras(task_id, task)
    if extras["anchor"] is None:
        return None
    return extras["anchor"].astimezone(ZoneInfo(config.USER_TIMEZONE)).strftime("%H:%M")


async def reschedule(task_id: str, day: date, new_time: str | None = KEEP, task: dict | None = None) -> list[str]:
    """Переносит задачу на `day` вместе с напоминаниями и событием.

    new_time: KEEP — то же время в новый день; "ЧЧ:ММ" — новое время;
    None — без времени, напоминания и событие снимаются. Событие и
    напоминания сдвигаются на одну величину, поэтому длительность встречи и
    отступ напоминания сохраняются.

    Дата задачи — главное: её сбой — исключение. Сбой событий и напоминаний
    после этого не откатывает перенос, а возвращается списком предупреждений
    ("calendar", "reminder").
    """
    # Кривое время — ошибка до записи, а не после переноса даты.
    clock = time.fromisoformat(new_time) if new_time not in (None, KEEP) else None
    await ms_todo.update_task(task_id, due_date=day.isoformat())

    if new_time is None:
        await cleanup_task_extras(task_id)
        return []

    try:
        extras = await _extras(task_id, task)
    except Exception:
        # Дата уже перенесена — это не ошибка переноса, а несдвинутое время.
        logger.warning("reschedule: read extras for %s", task_id, exc_info=True)
        return ["reminder"]
    warnings = ["calendar"] if extras["event_error"] else []
    anchor = extras["anchor"]
    if anchor is None:
        return warnings

    tz = ZoneInfo(config.USER_TIMEZONE)
    if clock is None:
        clock = anchor.astimezone(tz).time()
    # Разница в UTC: вычитание двух времён одной зоны идёт по «настенным»
    # часам и при переводе часов дало бы ошибку на час.
    delta = datetime.combine(day, clock, tzinfo=tz).astimezone(timezone.utc) - anchor.astimezone(timezone.utc)
    if delta == timedelta(0):
        return warnings

    if extras["start"] is not None:
        try:
            await google_calendar.update_event(
                extras["event_id"], (extras["start"] + delta).astimezone(tz), (extras["end"] + delta).astimezone(tz)
            )
        except Exception:
            logger.warning("reschedule: move event %s", extras["event_id"], exc_info=True)
            warnings.append("calendar")
    try:
        for rid, fire in extras["tg"]:
            await storage.set_reminder_fire_at(rid, _utc_str(fire + delta))
        if extras["ms"] is not None:
            await ms_todo.set_reminder(task_id, _utc_str(extras["ms"] + delta))
    except Exception:
        logger.warning("reschedule: move reminders for %s", task_id, exc_info=True)
        warnings.append("reminder")
    return warnings


async def _move(tasks: list[dict], day: date) -> list[dict]:
    """Переносит по одной; сбой одной задачи не останавливает остальные.

    Возвращает перенесённые.
    """
    moved = []
    for t in tasks:
        try:
            await reschedule(t["id"], day, KEEP, task=t)
            moved.append(t)
        except Exception:
            logger.warning("move: %s", t["id"], exc_info=True)
    return moved


async def move_today_to_tomorrow() -> list[dict]:
    """Все незавершённые задачи с сегодня — на завтра, в то же время."""
    return await _move(await ms_todo.get_tasks_today(), config.local_today() + timedelta(days=1))


async def move_overdue_to_today() -> list[dict]:
    """Все просроченные задачи — на сегодня, в то же время."""
    return await _move(await ms_todo.get_overdue_tasks(), config.local_today())
