"""API ритуалов разбора: шаги утреннего разбора, план дня, отметка «пройден».

Перенос выбранных задач — общий `POST /api/tasks/move` с `keys`.
"""
import logging
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web

import config
from api.routes import _serialize
from db import storage
from services import google_calendar, ms_todo, rituals

logger = logging.getLogger(__name__)

NORM_DAYS = 30
# Норма дня по дате: считается раз в сутки, это 1–2 запроса к Graph.
_norm_cache: dict[str, int | None] = {}


async def _items(tasks: list[dict]) -> list[dict]:
    items = await _serialize(tasks)
    for item, task in zip(items, tasks):
        item["created"] = task.get("createdDateTime")
    return items


async def get_morning(request: web.Request) -> web.Response:
    settings = await storage.get_ritual_settings(config.TELEGRAM_USER_ID)
    s = rituals.morning_slices(await ms_todo.get_all_tasks(), config.local_today(), settings["stuck_days"])
    return web.json_response({k: await _items(s[k]) for k in ("overdue", "stuck", "inbox")})


def _local_time(dt_str: str | None) -> str | None:
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str).astimezone(ZoneInfo(config.USER_TIMEZONE)).strftime("%H:%M")


async def _norm() -> int | None:
    today = config.local_today()
    key = today.isoformat()
    if key in _norm_cache:
        return _norm_cache[key]
    try:
        done = await ms_todo.get_completed_since(today - timedelta(days=NORM_DAYS))
    except Exception:
        logger.warning("ritual plan: norm unavailable", exc_info=True)
        return None
    if len(done) >= ms_todo.MAX_PAGES * 100:
        logger.warning("ritual plan: norm hit page limit (%d tasks)", len(done))
    days = Counter(ms_todo._local_date_of_utc((t.get("completedDateTime") or {}).get("dateTime", "")) for t in done)
    days.pop(key, None)  # сегодня ещё не закончилось
    days.pop(None, None)
    _norm_cache.clear()
    _norm_cache[key] = rituals.median_per_day(list(days.values()))
    return _norm_cache[key]


async def get_plan(request: web.Request) -> web.Response:
    """План дня: встречи, задачи на сегодня (со временем, если есть) и норма.

    Задача, у которой есть событие в календаре, — одна строка задачи со
    временем события, а не ещё и встреча.
    """
    today = config.local_today()
    tasks = rituals.morning_slices(await ms_todo.get_all_tasks(), today, 10_000)["today"]
    linked = await storage.get_linked_event_ids()
    try:
        events = await google_calendar.get_events_day(today)
    except Exception:
        logger.warning("ritual plan: calendar unavailable", exc_info=True)
        events = None

    starts = {e.get("id"): (e.get("start") or {}).get("dateTime") for e in events or []}
    items = await _items(tasks)
    for item, task in zip(items, tasks):
        event_id = await storage.get_calendar_link(task["id"])
        time_ = _local_time(starts.get(event_id)) if event_id else None
        if time_ is None:
            reminders = await storage.get_task_reminders(task["id"])
            if reminders:
                time_ = _local_time(reminders[0][1] + "+00:00")
        item["time"] = time_

    meetings = None
    if events is not None:
        meetings = [
            {"time": _local_time((e.get("start") or {}).get("dateTime")), "title": e.get("summary") or ""}
            for e in rituals.meetings(events, linked)
        ]
        meetings.sort(key=lambda m: m["time"] or "")
    return web.json_response({"events": meetings, "today": items, "norm": await _norm()})


async def post_done(request: web.Request) -> web.Response:
    kind = request.match_info["kind"]
    if kind not in rituals.KINDS:
        raise web.HTTPNotFound(text='{"error": "not_found", "message": "Нет такого ритуала"}', content_type="application/json")
    now = datetime.now(ZoneInfo(config.USER_TIMEZONE))
    await rituals.mark_done(kind, now.date().isoformat(), now)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/ritual/morning", get_morning)
    app.router.add_get("/api/ritual/plan", get_plan)
    app.router.add_post("/api/ritual/{kind}/done", post_done)
