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
    app.middlewares.append(error_middleware)
    app.middlewares.append(auth_middleware)
    app.router.add_get("/health", health)
    app.router.add_get("/api/inbox", get_inbox)
    app.router.add_post("/api/tasks/{key}/schedule", schedule_task)
    app.router.add_delete("/api/tasks/{key}", delete_task)
