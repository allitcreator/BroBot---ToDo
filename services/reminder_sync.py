"""Синк напоминаний MS Todo -> Telegram.

С Mac напоминание ставится в самой задаче (`reminderDateTime` в UTC,
`isReminderOn`). Раз в несколько минут бот читает открытые задачи и заводит
недостающие записи в `reminders`; дальше их отправляет обычный цикл.

Ключ записи — `task_id` + `fire_at`. Снимает синк только зеркальные записи
(source sync/mstodo/button): напоминания из чата без MS Todo (task_id пустой,
source='tg') и старые записи без source он не трогает.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from db import storage
from services import ms_todo

logger = logging.getLogger(__name__)

# Прошедшее время заводим не раньше последнего успешного синка и не глубже
# MAX_PAST: так не теряется напоминание, поставленное «через минуту» или
# пока бот лежал. Доставленное не вернётся: пара лежит в reminder_log. Первый
# проход (нет метки синка, журнал пуст) идёт с окном 0 — иначе MS Todo, который
# помнит доставленное старым механизмом время, дал бы один дубль.
MAX_PAST = timedelta(hours=6)
LAST_OK_KEY = "reminder_last_sync"
FMT = "%Y-%m-%dT%H:%M:%S"


def _fire_at_utc(task: dict) -> datetime | None:
    """reminderDateTime задачи как aware-UTC; None — напоминания нет.

    Нераспознанное время (мусор, неизвестная зона) — ValueError: вызывающий
    пропускает задачу и её строки не снимает.
    """
    if not task.get("isReminderOn"):
        return None
    rdt = task.get("reminderDateTime") or {}
    raw = (rdt.get("dateTime") or "").split(".")[0]
    if not raw:
        return None
    try:
        tz = ZoneInfo(rdt.get("timeZone") or "UTC")
        return datetime.fromisoformat(raw).replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception as e:
        raise ValueError(f"reminderDateTime {rdt!r}") from e


async def _window_start(now: datetime) -> datetime:
    raw = await storage.get_sync_state(LAST_OK_KEY)
    if not raw:
        return now
    try:
        last = datetime.strptime(raw, FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return now
    return min(now, max(last, now - MAX_PAST))


async def sync(now: datetime | None = None):
    """Один проход. Ошибка Graph пробрасывается — записи при этом не трогаются."""
    now = now or datetime.now(timezone.utc)
    # Снимаем только строки, существовавшие до запроса задач: строка, которую
    # за время запроса поставила кнопка («+1 ч»), в ответе Graph ещё не видна.
    before = {rid for rid, _, _ in await storage.get_sync_reminders()}
    tasks = await ms_todo.get_all_tasks()
    truncated = ms_todo.last_fetch_truncated()
    since = await _window_start(now)

    active: set[tuple[str, str]] = set()
    protected: set[str] = set()   # задачи с нечитаемым временем: их строки не трогаем
    for t in tasks:
        if t.get("status") == "completed":
            continue
        try:
            fire = _fire_at_utc(t)
        except ValueError:
            logger.warning("Синк: не разобрал напоминание задачи %s", t.get("id"))
            protected.add(t.get("id"))
            continue
        if fire is None:
            continue
        pair = (t["id"], fire.strftime(FMT))
        active.add(pair)
        if fire < since:
            continue
        if await storage.reminder_pair_known(*pair):
            continue
        await storage.save_reminder(
            config.TELEGRAM_USER_ID, pair[1], f"⏰ Напоминание: {t.get('title', '')}",
            task_id=pair[0], source="sync",
        )
        logger.info("Синк: напоминание задачи %s на %s UTC", pair[0], pair[1])

    # Снято, перенесено или задача закрыта — снимаем зеркальные записи.
    # Усечённый список (больше MAX_PAGES страниц) не доказывает отсутствие задачи.
    if truncated:
        logger.warning("Синк: список задач усечён, строки не снимаю")
    else:
        for reminder_id, task_id, fire_at in await storage.get_sync_reminders():
            if reminder_id not in before or task_id in protected or (task_id, fire_at) in active:
                continue
            await storage.delete_reminder(reminder_id)
            logger.info("Синк: напоминание задачи %s на %s UTC снято в MS Todo", task_id, fire_at)
    await storage.set_sync_state(LAST_OK_KEY, now.strftime(FMT))
    await storage.prune_reminder_log()
