"""API мини-аппа, этап D: настройки, статистика, проекты.

Бриф проекта пишется целиком (прочитать заметку → поменять секцию →
записать всё), поэтому все записи одного проекта идут под его замком:
параллельные «Дополнить» и «Разведать» иначе затирали бы друг друга.
Разведка долгая (Perplexity, до 90 с) и идёт фоновой задачей со статусом:
держать HTTP-запрос столько нельзя — nginx отдаст 504, а запись всё равно
случится без ведома пользователя.
"""
import asyncio
import logging
from collections import defaultdict

from aiohttp import web

import config
from db import storage
from services import ms_todo, projects, research

logger = logging.getLogger(__name__)

CONFIRM_MODES = ("all", "uncertain", "off")

_project_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
# Статус разведки по id проекта: running | done | error. В памяти: после
# рестарта статус пропадает, но результат уже лежит в заметке проекта.
_research_jobs: dict[str, dict] = {}
_background: set[asyncio.Task] = set()


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


# --- настройки ---

RITUAL_FLAGS = ("morning_enabled", "evening_enabled", "weekend_rituals")
RITUAL_TIMES = ("morning_time", "evening_time")
STUCK_DAYS = (7, 14, 30)


def _valid_ritual_time(value) -> bool:
    """«ЧЧ:ММ» с шагом 30 минут: сводка проверяется по получасам."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    return hh.isdigit() and mm in ("00", "30") and int(hh) < 24


async def _settings() -> dict:
    uid = config.TELEGRAM_USER_ID
    return {
        "confirm_mode": await storage.get_confirm_mode(uid),
        "research_enabled": await storage.get_research_enabled(uid),
        "projects_enabled": await storage.get_projects_enabled(uid),
        **await storage.get_ritual_settings(uid),
    }


async def get_settings(request: web.Request) -> web.Response:
    return web.json_response(await _settings())


async def patch_settings(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        body = None
    allowed = {"confirm_mode", "research_enabled", "projects_enabled", *RITUAL_FLAGS, *RITUAL_TIMES, "stuck_days"}
    if not isinstance(body, dict) or not body or set(body) - allowed:
        return _json_error(400, "bad_settings", f"Можно менять: {', '.join(sorted(allowed))}")
    if "confirm_mode" in body and body["confirm_mode"] not in CONFIRM_MODES:
        return _json_error(400, "bad_settings", f"confirm_mode: {', '.join(CONFIRM_MODES)}")
    for flag in ("research_enabled", "projects_enabled", *RITUAL_FLAGS):
        if flag in body and not isinstance(body[flag], bool):
            return _json_error(400, "bad_settings", f"{flag}: true или false")
    for field in RITUAL_TIMES:
        if field in body and not _valid_ritual_time(body[field]):
            return _json_error(400, "bad_settings", f"{field}: ЧЧ:00 или ЧЧ:30")
    if "stuck_days" in body and (type(body["stuck_days"]) is not int or body["stuck_days"] not in STUCK_DAYS):
        return _json_error(400, "bad_settings", f"stuck_days: {', '.join(map(str, STUCK_DAYS))}")

    uid = config.TELEGRAM_USER_ID
    if "confirm_mode" in body:
        await storage.set_confirm_mode(uid, body["confirm_mode"])
    if "research_enabled" in body:
        await storage.set_research_enabled(uid, body["research_enabled"])
    if "projects_enabled" in body:
        await storage.set_projects_enabled(uid, body["projects_enabled"])
    rituals = {k: v for k, v in body.items() if k in (*RITUAL_FLAGS, *RITUAL_TIMES, "stuck_days")}
    if rituals:
        await storage.set_ritual_settings(uid, rituals)
    return web.json_response(await _settings())


async def get_stats(request: web.Request) -> web.Response:
    return web.json_response(await ms_todo.get_stats())


# --- проекты ---

@web.middleware
async def projects_enabled_middleware(request: web.Request, handler):
    if request.path.startswith("/api/projects") and not await storage.get_projects_enabled(config.TELEGRAM_USER_ID):
        return _json_error(403, "projects_disabled", "Проекты выключены в настройках")
    return await handler(request)


async def _project_id(request: web.Request) -> str:
    task_id = await storage.resolve_task_id(request.match_info["key"])
    if not task_id:
        raise web.HTTPNotFound(
            text='{"error": "not_found", "message": "Проект не найден"}', content_type="application/json",
        )
    return task_id


def _first_line(text: str) -> str:
    text = (text or "").strip()
    if text in ("", projects.EMPTY):
        return ""
    return text.splitlines()[0].strip()[:160]


async def list_projects(request: web.Request) -> web.Response:
    items = []
    for t in sorted(await projects.list_projects(), key=lambda t: t.get("createdDateTime") or "", reverse=True):
        brief = projects.parse_body((t.get("body") or {}).get("content") or "")
        items.append({
            "key": await storage.register_task_id(t["id"]),
            "title": t.get("title") or "",
            "summary": _first_line(brief.get("why", "")),
        })
    return web.json_response({"items": items})


async def get_project(request: web.Request) -> web.Response:
    task_id = await _project_id(request)
    project = await projects.get_project(task_id)
    brief = project["brief"]
    sections = []
    for key, title in projects.SECTIONS:
        value = (brief.get(key) or "").strip()
        if key in projects.OPTIONAL_SECTIONS and not value:
            continue
        sections.append({"key": key, "title": title, "value": "" if value == projects.EMPTY else value})
    job = _research_jobs.get(task_id)
    return web.json_response({
        "title": brief.get("title") or "",
        "sections": sections,
        "steps": project["steps"],
        "body_valid": project["body_valid"],
        "research_enabled": await storage.get_research_enabled(config.TELEGRAM_USER_ID),
        "research": job or {"status": "idle"},
    })


async def toggle_step(request: web.Request) -> web.Response:
    task_id = await _project_id(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body.get("checked"), bool):
        return _json_error(400, "bad_step", "checked: true или false")
    await ms_todo.update_checklist_item(
        task_id, request.match_info["item"], body["checked"], config.MS_PROJECTS_LIST_ID,
    )
    return web.json_response({"ok": True})


async def append_section(request: web.Request) -> web.Response:
    task_id = await _project_id(request)
    key = request.match_info["section"]
    if key not in projects.SECTION_KEYS:
        return _json_error(400, "bad_section", "Нет такой секции брифа")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    text = str(body.get("text") or "").strip()
    if not text:
        return _json_error(400, "empty", "Пустой текст")

    async with _project_locks[task_id]:
        project = await projects.get_project(task_id)
        # Заметку правили руками — шаблоном её не пересобираем, иначе всё,
        # что не легло в известные секции, пропадёт молча.
        if not project["body_valid"]:
            return _json_error(409, "body_edited", "Заметку проекта правили руками — дополни её в MS To Do")
        await projects.append_to_section(task_id, key, text)
    return web.json_response({"ok": True})


async def _run_research(task_id: str):
    job = _research_jobs[task_id]
    try:
        async with _project_locks[task_id]:
            project = await projects.get_project(task_id)
            result = await research.research(project["brief"].get("title", ""), project["brief"])
            if result["error"] or not result["summary"]:
                job.update(status="error", message=result["error"] or "пустой ответ")
                return
            await projects.set_section(task_id, "research", research.render_section(result))
        job.update(status="done")
    except Exception as e:
        logger.exception("mini-app research failed for %s", task_id)
        job.update(status="error", message=str(e)[:200])


async def start_research(request: web.Request) -> web.Response:
    task_id = await _project_id(request)
    if not await storage.get_research_enabled(config.TELEGRAM_USER_ID):
        return _json_error(403, "research_disabled", "Разведка выключена в настройках")
    if _research_jobs.get(task_id, {}).get("status") == "running":
        return web.json_response(_research_jobs[task_id], status=202)

    _research_jobs[task_id] = {"status": "running"}
    task = asyncio.create_task(_run_research(task_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return web.json_response(_research_jobs[task_id], status=202)


async def research_status(request: web.Request) -> web.Response:
    task_id = await _project_id(request)
    return web.json_response(_research_jobs.get(task_id) or {"status": "idle"})


def setup(app: web.Application) -> None:
    app.middlewares.append(projects_enabled_middleware)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_patch("/api/settings", patch_settings)
    app.router.add_get("/api/stats", get_stats)
    app.router.add_get("/api/projects", list_projects)
    app.router.add_get("/api/projects/{key}", get_project)
    app.router.add_patch("/api/projects/{key}/steps/{item}", toggle_step)
    app.router.add_post("/api/projects/{key}/sections/{section}", append_section)
    app.router.add_post("/api/projects/{key}/research", start_research)
    app.router.add_get("/api/projects/{key}/research", research_status)
