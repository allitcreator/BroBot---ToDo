"""HTTP API мини-аппа. Живёт в том же aiohttp-приложении, что и вебхук.

Наружу отдаются только нужные поля и короткие ключи задач (`task_ids`), а
не сырые объекты и id Graph. Ошибки внешних сервисов — кодом 502 с
коротким текстом; подробности уходят в лог.
"""
import logging
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web

import config
from api import material_routes
from api.auth import InitDataError, verify_init_data
from db import storage
from services import google_calendar, ms_todo, task_meta, task_ops

logger = logging.getLogger(__name__)

NOTE_LIMIT = 300


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    # Вход с Mac живёт на своём токене и ничего другого не открывает.
    if request.path.startswith(material_routes.ASSISTANT_PREFIX):
        return await material_routes.bearer_gate(request, handler)

    header = request.headers.get("Authorization", "")
    init_data = header[4:] if header.startswith("tma ") else ""
    try:
        fields = verify_init_data(init_data, config.TELEGRAM_BOT_TOKEN)
    except InitDataError as exc:
        logger.info("mini-app auth rejected: %s (%r)", exc, request.path)
        return web.json_response({"error": "auth", "message": str(exc)}, status=401)

    if int(fields["user"].get("id", 0)) != config.TELEGRAM_USER_ID:
        logger.warning("mini-app: foreign user %s", fields["user"].get("id"))
        return web.json_response({"error": "forbidden", "message": "Это личный бот"}, status=403)

    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler):
    # Вебхук живёт в том же приложении — его ошибки не трогаем.
    if not request.path.startswith("/api/"):
        return await handler(request)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except ms_todo.AuthExpiredError:
        logger.error("mini-app: MS token expired", exc_info=True)
        return web.json_response(
            {"error": "auth_expired", "message": "MS To Do не принимает токен — перевыпусти через get_tokens.py"},
            status=502,
        )
    except Exception:
        logger.exception("mini-app: %s %r failed", request.method, request.path)
        return web.json_response(
            {"error": "upstream", "message": "MS To Do не ответил, попробуй ещё раз"},
            status=502,
        )


def _note(task: dict) -> str:
    # Служебный блок снимается до обрезки, иначе его кусок попадёт в текст.
    text = task_meta.split((task.get("body") or {}).get("content"))[0].strip()
    return text if len(text) <= NOTE_LIMIT else text[:NOTE_LIMIT] + "…"


def _meta(task: dict) -> task_meta.Meta:
    return task_meta.split((task.get("body") or {}).get("content"))[1]


def _meta_fields(task: dict, today: date) -> dict:
    """Номер на сегодня, исполнитель и рекомендации из служебного блока."""
    meta = _meta(task)
    return {"order": meta.order_on(today), "executor": meta.executor, "advice": meta.advice}


def local_time(dt_str: str | None) -> str | None:
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str).astimezone(ZoneInfo(config.USER_TIMEZONE)).strftime("%H:%M")


async def task_times(tasks: list[dict], starts: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """Время задачи «ЧЧ:ММ»: из события календаря (`starts` — начала событий
    по id), иначе из первого напоминания бота."""
    starts = starts or {}
    times = {}
    for task in tasks:
        event_id = await storage.get_calendar_link(task["id"])
        time_ = local_time(starts.get(event_id)) if event_id else None
        if time_ is None:
            reminders = await storage.get_task_reminders(task["id"])
            if reminders:
                time_ = local_time(reminders[0][1] + "+00:00")
        times[task["id"]] = time_
    return times


async def calendar_starts(tasks: list[dict], day: date) -> dict[str, str | None]:
    """Начала событий дня по id — только если у задач есть привязанные
    события: иначе в Google не ходим. Сбой календаря — без времени событий."""
    if not await storage.get_task_ids_in_calendar([t["id"] for t in tasks]):
        return {}
    try:
        events = await google_calendar.get_events_day(day)
    except Exception:
        logger.warning("mini-app: calendar unavailable for day order", exc_info=True)
        return {}
    return {e.get("id"): (e.get("start") or {}).get("dateTime") for e in events or []}


def day_order(tasks: list[dict], times: dict[str, str | None], today: date) -> list[dict]:
    """Порядок дня: с номером — по номеру, дальше со временем — по времени,
    потом старые раньше новых. Один на «Сегодня», план дня и вечер."""
    def key(task):
        order = _meta(task).order_on(today)
        time_ = times.get(task["id"])
        return (order is None, order or 0, time_ is None, time_ or "", task.get("createdDateTime") or "")

    return sorted(tasks, key=key)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def get_inbox(request: web.Request) -> web.Response:
    tasks = await ms_todo.get_inbox_tasks()
    items = []
    for t in sorted(tasks, key=lambda t: t.get("createdDateTime") or ""):
        items.append({
            "key": await storage.register_task_id(t["id"]),
            "title": t.get("title") or "",
            "note": _note(t),
            "created": t.get("createdDateTime"),
            **_meta_fields(t, config.local_today()),
        })
    return web.json_response({"count": len(items), "items": items})


async def _serialize(tasks: list[dict]) -> list[dict]:
    """Задачи в вид для мини-аппа: ключ, название, заметка, дата и значки."""
    ids = [t["id"] for t in tasks]
    with_reminder = await storage.get_task_ids_with_any_reminder(ids)
    in_calendar = await storage.get_task_ids_in_calendar(ids)
    today = config.local_today()
    out = []
    for t in tasks:
        out.append({
            "key": await storage.register_task_id(t["id"]),
            "title": t.get("title") or "",
            "note": _note(t),
            "date": ms_todo.task_local_date(t),
            "reminder": t["id"] in with_reminder or bool(t.get("isReminderOn")),
            "calendar": t["id"] in in_calendar,
            **_meta_fields(t, today),
        })
    return out


def _by_date(tasks: list[dict]) -> list[dict]:
    return sorted(tasks, key=lambda t: (ms_todo.task_local_date(t) or "", t.get("createdDateTime") or ""))


async def get_tasks(request: web.Request) -> web.Response:
    view = request.query.get("view")
    if view not in ("today", "week"):
        return web.json_response({"error": "bad_view", "message": "view: today или week"}, status=400)
    today = config.local_today()
    tasks = await ms_todo.get_all_tasks()
    dated = [(ms_todo.task_local_date(t), t) for t in tasks]

    if view == "today":
        overdue = [t for d, t in dated if d and d < today.isoformat()]
        todays = [t for d, t in dated if d == today.isoformat()]
        return web.json_response({
            "overdue": await _serialize(_by_date(overdue)),
            "today": await _serialize(day_order(todays, await task_times(todays, await calendar_starts(todays, today)), today)),
        })

    days = []
    for i in range(7):
        day = (today + timedelta(days=i)).isoformat()
        items = [t for d, t in dated if d == day]
        days.append({"date": day, "items": await _serialize(_by_date(items))})
    return web.json_response({"days": days})


async def get_summary(request: web.Request) -> web.Response:
    """Счётчики для вкладок — из одного (закэшированного) списка задач."""
    today = config.local_today().isoformat()
    dates = [ms_todo.task_local_date(t) for t in await ms_todo.get_all_tasks()]
    return web.json_response({
        "inbox": sum(1 for d in dates if d is None),
        "today": sum(1 for d in dates if d == today),
        "overdue": sum(1 for d in dates if d and d < today),
        "reading": await storage.count_unread_materials(),
    })


async def complete_task(request: web.Request) -> web.Response:
    task_id = await _task_id_or_404(request)
    await ms_todo.complete_task(task_id)
    await task_ops.cleanup_task_extras(task_id)
    return web.json_response({"ok": True})


async def move_tasks(request: web.Request) -> web.Response:
    """Массовый перенос: просроченные или сегодняшние — на сегодня или завтра,
    либо выбранные ключи (`keys`) — на любой день.

    Сбой на одной задаче не останавливает остальные: в ответе итог
    «перенесено X из Y» и названия тех, что не удалось.
    """
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    today = config.local_today()

    if "keys" in body:
        # Выбранные задачи (ритуал): to — today, tomorrow или ГГГГ-ММ-ДД.
        keys = body["keys"]
        target = _parse_day(str(body.get("to", "")))
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys) or target is None:
            return web.json_response({"error": "bad_move", "message": "keys: [ключи], to: today|tomorrow|ГГГГ-ММ-ДД"}, status=400)
        by_id = {t["id"]: t for t in await ms_todo.get_all_tasks()}
        chosen = []
        for key in keys:
            task_id = await storage.resolve_task_id(key)
            if not task_id:
                raise web.HTTPNotFound(
                    text='{"error": "not_found", "message": "Задача не найдена"}', content_type="application/json",
                )
            chosen.append(by_id.get(task_id) or {"id": task_id})
    else:
        scope, to = body.get("scope"), body.get("to")
        if scope not in ("overdue", "today") or to not in ("today", "tomorrow") or (scope, to) == ("today", "today"):
            return web.json_response({"error": "bad_move", "message": "scope: overdue|today, to: today|tomorrow"}, status=400)
        target = today + timedelta(days=1 if to == "tomorrow" else 0)
        tasks = await ms_todo.get_all_tasks()
        if scope == "overdue":
            chosen = [t for t in tasks if (d := ms_todo.task_local_date(t)) and d < today.isoformat()]
        else:
            chosen = [t for t in tasks if ms_todo.task_local_date(t) == today.isoformat()]

    # Время на задачу: {ключ: "ЧЧ:ММ" | null}. Нет ключа — то же время.
    raw_times = body.get("times") or {}
    if not isinstance(raw_times, dict):
        return web.json_response({"error": "bad_move", "message": "times: {ключ: ЧЧ:ММ или null}"}, status=400)
    times = {}
    for key, value in raw_times.items():
        task_id = await storage.resolve_task_id(key)
        if not task_id or not _valid_time(value):
            return web.json_response({"error": "bad_time", "message": "Время: ЧЧ:ММ или null"}, status=400)
        times[task_id] = value

    moved, failed, warnings = 0, [], set()
    for t in chosen:
        try:
            warnings.update(await task_ops.reschedule(t["id"], target, times.get(t["id"], task_ops.KEEP), task=t if "title" in t else None))
            moved += 1
        except Exception:
            logger.warning("mini-app move: %s", t["id"], exc_info=True)
            failed.append(t.get("title") or "")
    result = {"moved": moved, "total": len(chosen), "failed": failed}
    if warnings:
        result["warnings"] = sorted(warnings)
    return web.json_response(result)


def _bad_plan(message: str, error: str = "bad_plan") -> web.Response:
    return web.json_response({"error": error, "message": message}, status=400)


async def save_plan(request: web.Request) -> web.Response:
    """Порядок дня и исполнители одним запросом.

    `order` — ключи задач на сегодня по порядку: им номера 1…N, у остальных
    сегодняшних номер на сегодня снимается. `meta` — {ключ: {executor,
    advice}}, `null` снимает поле. По задаче — один GET и один PATCH, без
    изменений — ни одного. Сбой на задаче не останавливает остальные.
    """
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not ({"order", "meta"} & body.keys()):
        return _bad_plan("order: [ключи] и/или meta: {ключ: {executor, advice}}")
    order = body.get("order")
    meta = body.get("meta", {})
    if "order" in body and (not isinstance(order, list) or not all(isinstance(k, str) for k in order) or len(set(order)) != len(order)):
        return _bad_plan("order: список разных ключей")
    if not isinstance(meta, dict):
        return _bad_plan("meta: {ключ: {executor, advice}}")
    for fields in meta.values():
        if not isinstance(fields, dict) or not fields.keys() <= {"executor", "advice"}:
            return _bad_plan("meta: только executor и advice")
        if "executor" in fields and fields["executor"] not in (*task_meta.EXECUTORS, None):
            return _bad_plan("executor: agent, self или null")
        advice = fields.get("advice")
        if advice is not None and (not isinstance(advice, str) or len(advice) > task_meta.ADVICE_LIMIT):
            return _bad_plan(f"advice: строка до {task_meta.ADVICE_LIMIT} символов или null")

    async def resolve(key: str) -> str:
        task_id = await storage.resolve_task_id(key)
        if not task_id:
            raise web.HTTPNotFound(text='{"error": "not_found", "message": "Задача не найдена"}', content_type="application/json")
        return task_id

    today = config.local_today()
    tasks = {t["id"]: t for t in await ms_todo.get_all_tasks()}
    todays = {i for i, t in tasks.items() if ms_todo.task_local_date(t) == today.isoformat()}
    positions = {await resolve(k): n for n, k in enumerate(order or [], start=1)}
    if positions.keys() - todays:
        return _bad_plan("Номер ставится только задачам на сегодня", "not_today")
    fields_by_id = {await resolve(k): v for k, v in meta.items()}

    touched = list(positions) + [i for i in fields_by_id if i not in positions]
    if "order" in body:
        # У невыбранных сегодняшних номер снимаем — тех, у кого он есть.
        touched += [i for i in todays if i not in positions and i not in fields_by_id and _meta(tasks[i]).order_on(today) is not None]

    def change_for(task_id: str):
        def change(m: task_meta.Meta) -> task_meta.Meta:
            if m.order is not None and m.order_date != today:
                m = replace(m, order=None, order_date=None)  # вчерашний номер не нужен
            if "order" in body:
                if task_id in positions:
                    m = replace(m, order=positions[task_id], order_date=today)
                elif m.order_on(today) is not None:
                    m = replace(m, order=None, order_date=None)
            fields = fields_by_id.get(task_id, {})
            if "executor" in fields:
                m = replace(m, executor=fields["executor"])
            if "advice" in fields:
                m = replace(m, advice=(fields["advice"] or "").strip() or None)
            return m
        return change

    saved, failed = 0, []
    for task_id in touched:
        try:
            saved += await task_ops.update_meta(task_id, change_for(task_id))
        except Exception:
            logger.warning("mini-app plan: %s", task_id, exc_info=True)
            failed.append((tasks.get(task_id) or {}).get("title") or "")
    return web.json_response({"saved": saved, "total": len(touched), "failed": failed})


async def _task_id_or_404(request: web.Request) -> str:
    task_id = await storage.resolve_task_id(request.match_info["key"])
    if not task_id:
        raise web.HTTPNotFound(
            text='{"error": "not_found", "message": "Задача не найдена"}',
            content_type="application/json",
        )
    return task_id


def _valid_time(value) -> bool:
    """null или «ЧЧ:ММ» 00:00–23:59."""
    if value is None:
        return True
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    try:
        time.fromisoformat(value)
    except ValueError:
        return False
    return True


async def _cached_task(task_id: str) -> dict | None:
    """Задача из закэшированного списка открытых — без отдельного запроса.

    Мини-апп переносит только открытые задачи, а кэш сбрасывается любой
    записью, так что задача там есть. Нет — значит, её закрыли или удалили
    в To Do, и напоминания MS у неё не ищем.
    """
    return next((t for t in await ms_todo.get_all_tasks() if t["id"] == task_id), {})


def _parse_day(value: str) -> date | None:
    today = config.local_today()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


async def schedule_task(request: web.Request) -> web.Response:
    task_id = await _task_id_or_404(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    day = _parse_day(str(body.get("date", "")))
    if day is None:
        return web.json_response({"error": "bad_date", "message": "Дата: today, tomorrow или ГГГГ-ММ-ДД"}, status=400)
    # time: "ЧЧ:ММ" — новое время напоминаний и события, null — снять их,
    # нет поля — то же время в новый день.
    new_time = body.get("time", task_ops.KEEP)
    if new_time != task_ops.KEEP and not _valid_time(new_time):
        return web.json_response({"error": "bad_time", "message": "Время: ЧЧ:ММ или null"}, status=400)
    warnings = await task_ops.reschedule(task_id, day, new_time, task=await _cached_task(task_id))
    result = {"ok": True, "date": day.isoformat()}
    if warnings:
        result["warnings"] = warnings
    return web.json_response(result)


async def get_task_time(request: web.Request) -> web.Response:
    """Время задачи для поля при переносе: из события или напоминания."""
    task_id = await _task_id_or_404(request)
    return web.json_response({"time": await task_ops.task_time(task_id, task=await _cached_task(task_id))})


async def delete_task(request: web.Request) -> web.Response:
    task_id = await _task_id_or_404(request)
    await ms_todo.delete_task(task_id)
    await task_ops.cleanup_task_extras(task_id)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    from api import more_routes, ritual_routes

    app.middlewares.append(error_middleware)
    app.middlewares.append(auth_middleware)
    app.router.add_get("/health", health)
    app.router.add_get("/api/inbox", get_inbox)
    app.router.add_get("/api/tasks", get_tasks)
    app.router.add_get("/api/summary", get_summary)
    app.router.add_post("/api/tasks/move", move_tasks)
    app.router.add_post("/api/tasks/plan", save_plan)
    app.router.add_post("/api/tasks/{key}/complete", complete_task)
    app.router.add_post("/api/tasks/{key}/schedule", schedule_task)
    app.router.add_get("/api/tasks/{key}/time", get_task_time)
    app.router.add_delete("/api/tasks/{key}", delete_task)
    more_routes.setup(app)
    ritual_routes.setup(app)
    material_routes.setup(app)
