"""HTTP API мини-аппа. Живёт в том же aiohttp-приложении, что и вебхук.

Наружу отдаются только нужные поля и короткие ключи задач (`task_ids`), а
не сырые объекты и id Graph. Ошибки внешних сервисов — кодом 502 с
коротким текстом; подробности уходят в лог.
"""
import logging
from datetime import date, timedelta

from aiohttp import web

import config
from api.auth import InitDataError, verify_init_data
from db import storage
from services import ms_todo, task_ops

logger = logging.getLogger(__name__)

NOTE_LIMIT = 300


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)

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
    text = ((task.get("body") or {}).get("content") or "").strip()
    return text if len(text) <= NOTE_LIMIT else text[:NOTE_LIMIT] + "…"


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
        })
    return web.json_response({"count": len(items), "items": items})


async def _serialize(tasks: list[dict]) -> list[dict]:
    """Задачи в вид для мини-аппа: ключ, название, заметка, дата и значки."""
    ids = [t["id"] for t in tasks]
    with_reminder = await storage.get_task_ids_with_any_reminder(ids)
    in_calendar = await storage.get_task_ids_in_calendar(ids)
    out = []
    for t in tasks:
        out.append({
            "key": await storage.register_task_id(t["id"]),
            "title": t.get("title") or "",
            "note": _note(t),
            "date": ms_todo.task_local_date(t),
            "reminder": t["id"] in with_reminder or bool(t.get("isReminderOn")),
            "calendar": t["id"] in in_calendar,
        })
    return out


def _by_date(tasks: list[dict]) -> list[dict]:
    return sorted(tasks, key=lambda t: (ms_todo.task_local_date(t) or "", t.get("createdDateTime") or ""))


async def get_tasks(request: web.Request) -> web.Response:
    view = request.query.get("view")
    today = config.local_today()
    tasks = await ms_todo.get_all_tasks()
    dated = [(ms_todo.task_local_date(t), t) for t in tasks]

    if view == "today":
        overdue = [t for d, t in dated if d and d < today.isoformat()]
        todays = [t for d, t in dated if d == today.isoformat()]
        return web.json_response({
            "overdue": await _serialize(_by_date(overdue)),
            "today": await _serialize(_by_date(todays)),
        })

    if view == "week":
        days = []
        for i in range(7):
            day = (today + timedelta(days=i)).isoformat()
            items = [t for d, t in dated if d == day]
            days.append({"date": day, "items": await _serialize(_by_date(items))})
        return web.json_response({"days": days})

    return web.json_response({"error": "bad_view", "message": "view: today или week"}, status=400)


async def get_summary(request: web.Request) -> web.Response:
    """Счётчики для вкладок — из одного (закэшированного) списка задач."""
    today = config.local_today().isoformat()
    dates = [ms_todo.task_local_date(t) for t in await ms_todo.get_all_tasks()]
    return web.json_response({
        "inbox": sum(1 for d in dates if d is None),
        "today": sum(1 for d in dates if d == today),
        "overdue": sum(1 for d in dates if d and d < today),
    })


async def complete_task(request: web.Request) -> web.Response:
    task_id = await _task_id_or_404(request)
    await ms_todo.complete_task(task_id)
    await task_ops.cleanup_task_extras(task_id)
    return web.json_response({"ok": True})


async def move_tasks(request: web.Request) -> web.Response:
    """Массовый перенос: просроченные или сегодняшние — на сегодня или завтра.

    Сбой на одной задаче не останавливает остальные: в ответе итог
    «перенесено X из Y» и названия тех, что не удалось.
    """
    try:
        body = await request.json()
    except ValueError:
        body = {}
    scope, to = body.get("scope"), body.get("to")
    if scope not in ("overdue", "today") or to not in ("today", "tomorrow") or (scope, to) == ("today", "today"):
        return web.json_response({"error": "bad_move", "message": "scope: overdue|today, to: today|tomorrow"}, status=400)

    today = config.local_today()
    target = (today + timedelta(days=1 if to == "tomorrow" else 0)).isoformat()
    tasks = await ms_todo.get_all_tasks()
    if scope == "overdue":
        chosen = [t for t in tasks if (d := ms_todo.task_local_date(t)) and d < today.isoformat()]
    else:
        chosen = [t for t in tasks if ms_todo.task_local_date(t) == today.isoformat()]

    moved, failed = 0, []
    for t in chosen:
        try:
            await ms_todo.update_task(t["id"], due_date=target)
            moved += 1
        except Exception:
            logger.warning("mini-app move: %s", t["id"], exc_info=True)
            failed.append(t.get("title") or "")
    return web.json_response({"moved": moved, "total": len(chosen), "failed": failed})


async def _task_id_or_404(request: web.Request) -> str:
    task_id = await storage.resolve_task_id(request.match_info["key"])
    if not task_id:
        raise web.HTTPNotFound(
            text='{"error": "not_found", "message": "Задача не найдена"}',
            content_type="application/json",
        )
    return task_id


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
    await ms_todo.update_task(task_id, due_date=day.isoformat())
    return web.json_response({"ok": True, "date": day.isoformat()})


async def delete_task(request: web.Request) -> web.Response:
    task_id = await _task_id_or_404(request)
    await ms_todo.delete_task(task_id)
    await task_ops.cleanup_task_extras(task_id)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    from api import more_routes

    app.middlewares.append(error_middleware)
    app.middlewares.append(auth_middleware)
    app.router.add_get("/health", health)
    app.router.add_get("/api/inbox", get_inbox)
    app.router.add_get("/api/tasks", get_tasks)
    app.router.add_get("/api/summary", get_summary)
    app.router.add_post("/api/tasks/move", move_tasks)
    app.router.add_post("/api/tasks/{key}/complete", complete_task)
    app.router.add_post("/api/tasks/{key}/schedule", schedule_task)
    app.router.add_delete("/api/tasks/{key}", delete_task)
    more_routes.setup(app)
