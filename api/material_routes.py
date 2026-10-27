"""Материалы для чтения.

Два входа с разной авторизацией:
- /api/assistant/materials — с Mac (`todo.py publish`), Bearer-токен
  ASSISTANT_API_TOKEN. Токен открывает только этот путь. Пустой токен —
  эндпоинт выключен (404). В лог идут метод, путь и код ответа, без тела.
- /api/reading — мини-апп, подпись initData, как остальной API.
"""
import asyncio
import hmac
import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import web
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config
from db import storage

logger = logging.getLogger(__name__)

ASSISTANT_PREFIX = "/api/assistant/"
MAX_BODY = 256 * 1024
TITLE_MAX = 200
SOURCE_MAX = 300
_KEY_RE = re.compile(r"[0-9a-f]{32}")
_ID_RE = re.compile(r"[0-9]{1,18}")


def _json(status: int, error: str, message: str) -> web.Response:
    return web.json_response({"error": error, "message": message}, status=status)


def _token_ok(header: str) -> bool:
    expected = config.ASSISTANT_API_TOKEN
    if not header.startswith("Bearer "):
        return False
    try:
        # compare_digest на не-ASCII str падает с TypeError — сравниваем байты;
        # суррогаты из невалидного UTF-8 в заголовке не кодируются — это отказ.
        return hmac.compare_digest(header[7:].encode("utf-8"), expected.encode("utf-8"))
    except (UnicodeError, TypeError):
        return False


@web.middleware
async def bearer_gate(request: web.Request, handler):
    """Пускает /api/assistant/* только с верным Bearer; логирует метод, путь, код.

    Путь пишется через %r: управляющие символы в URL не подделают строку лога.
    """
    if not config.ASSISTANT_API_TOKEN:
        resp = _json(404, "disabled", "Не найдено")
    elif not _token_ok(request.headers.get("Authorization", "")):
        resp = _json(401, "auth", "Нужен токен")
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            logger.info("assistant %s %r -> %s", request.method, request.path, exc.status)
            raise
        except Exception:
            # Без тела запроса в логе; ответ свой, а не «MS To Do не ответил».
            logger.error("assistant %s %r -> 500", request.method, request.path, exc_info=True)
            resp = _json(500, "internal", "Ошибка сервера")
    logger.info("assistant %s %r -> %s", request.method, request.path, resp.status)
    return resp


async def _read_body(request: web.Request) -> bytes | None:
    """Тело не больше MAX_BODY; None — превышение."""
    if request.content_length is not None and request.content_length > MAX_BODY:
        return None
    data = b""
    async for chunk in request.content.iter_chunked(64 * 1024):
        data += chunk
        if len(data) > MAX_BODY:
            return None
    return data


def _validate(payload) -> tuple[dict | None, str]:
    if not isinstance(payload, dict):
        return None, "Тело должно быть JSON-объектом"
    title, markdown = payload.get("title"), payload.get("markdown")
    source, key = payload.get("source", ""), payload.get("idempotency_key")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= TITLE_MAX or len(title) > TITLE_MAX:
        return None, f"title: строка 1–{TITLE_MAX} символов"
    if not isinstance(markdown, str) or not markdown.strip():
        return None, "markdown: непустая строка"
    if not isinstance(source, str) or len(source) > SOURCE_MAX:
        return None, f"source: строка до {SOURCE_MAX} символов"
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        return None, "idempotency_key: 32 символа hex"
    return {"title": title.strip(), "markdown": markdown, "source": source, "key": key}, ""


def read_url(base: str, material_id: int) -> str:
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "read"] + [("read", str(material_id))]
    return urlunsplit(parts._replace(query=urlencode(query)))


async def _notify(request: web.Request, material_id: int, title: str) -> bool:
    """«📖 Новое: …» с кнопкой в мини-апп. Сбой Telegram публикацию не отменяет;
    возвращает, ушло ли сообщение."""
    bot = request.app.get("bot")
    if bot is None or not config.TELEGRAM_USER_ID:
        return False
    markup = None
    if config.MINIAPP_URL:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Читать ↗", web_app=WebAppInfo(url=read_url(config.MINIAPP_URL, material_id))),
        ]])
    try:
        await asyncio.wait_for(
            bot.send_message(config.TELEGRAM_USER_ID, f"📖 Новое: {title}", reply_markup=markup), 15,
        )
    except Exception:
        logger.warning("material %s: уведомление не ушло", material_id, exc_info=True)
        return False
    return True


async def publish(request: web.Request) -> web.Response:
    raw = await _read_body(request)
    if raw is None:
        return _json(413, "too_large", f"Тело больше {MAX_BODY // 1024} КБ")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _json(400, "bad_json", "Тело не JSON (UTF-8)")
    data, problem = _validate(payload)
    if data is None:
        return _json(400, "bad_request", problem)
    material_id, duplicate = await storage.create_material(data["title"], data["markdown"], data["source"], data["key"])
    # Повтор уведомления не шлём; notified говорит, ушло ли оно при этой публикации.
    notified = False if duplicate else await _notify(request, material_id, data["title"])
    return web.json_response(
        {"id": material_id, "duplicate": duplicate, "notified": notified}, status=200 if duplicate else 201,
    )


async def assistant_list(request: web.Request) -> web.Response:
    return web.json_response({"items": await storage.list_materials()})


def _id(request: web.Request) -> int:
    raw = request.match_info["id"]
    if _ID_RE.fullmatch(raw):
        return int(raw)
    raise web.HTTPNotFound(text='{"error": "not_found", "message": "Нет такого материала"}',
                           content_type="application/json")


def _not_found() -> web.Response:
    return _json(404, "not_found", "Нет такого материала")


async def assistant_delete(request: web.Request) -> web.Response:
    if not await storage.delete_material(_id(request)):
        return _not_found()
    return web.json_response({"ok": True})


# --- мини-апп ---

def _public(item: dict) -> dict:
    out = {k: item[k] for k in ("id", "title", "source", "created_at", "pinned", "chars")}
    out["read"] = item["read_at"] is not None
    return out


async def reading_list(request: web.Request) -> web.Response:
    items = await storage.list_materials()
    return web.json_response({
        "unread": sum(1 for i in items if i["read_at"] is None),
        "items": [_public(i) for i in items],
    })


async def reading_get(request: web.Request) -> web.Response:
    item = await storage.get_material(_id(request))
    if item is None:
        return _not_found()
    return web.json_response({**_public(item), "markdown": item["markdown"]})


async def _flag(request: web.Request, field: str, setter) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    value = body.get(field, True) if isinstance(body, dict) else True
    if not isinstance(value, bool):
        return _json(400, "bad_request", f"{field}: true или false")
    if not await setter(_id(request), value):
        return _not_found()
    return web.json_response({"ok": True})


async def reading_read(request: web.Request) -> web.Response:
    return await _flag(request, "read", storage.set_material_read)


async def reading_pin(request: web.Request) -> web.Response:
    return await _flag(request, "pinned", storage.set_material_pinned)


async def reading_delete(request: web.Request) -> web.Response:
    return await assistant_delete(request)


def setup(app: web.Application) -> None:
    app.router.add_post("/api/assistant/materials", publish)
    app.router.add_get("/api/assistant/materials", assistant_list)
    app.router.add_delete("/api/assistant/materials/{id}", assistant_delete)
    app.router.add_get("/api/reading", reading_list)
    app.router.add_get("/api/reading/{id}", reading_get)
    app.router.add_post("/api/reading/{id}/read", reading_read)
    app.router.add_post("/api/reading/{id}/pin", reading_pin)
    app.router.add_delete("/api/reading/{id}", reading_delete)
