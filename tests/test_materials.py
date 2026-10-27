"""Материалы для чтения: вход с Mac по Bearer-токену и чтение из мини-аппа.

Токен даёт доступ только к /api/assistant/materials; всё остальное по-прежнему
требует подпись initData. Пустой токен — эндпоинт выключен.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from api import routes  # noqa: E402
from db import storage  # noqa: E402
from test_api import OWNER, TOKEN, _auth, loop  # noqa: E402,F401

API_TOKEN = "s3cret-token-for-tests"
KEY = "0123456789abcdef0123456789abcdef"
URL = "/api/assistant/materials"


class FakeBot:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if self.fail:
            raise RuntimeError("Telegram down")
        self.sent.append((chat_id, text, reply_markup))


@pytest.fixture
def env(loop, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER)
    monkeypatch.setattr(config, "ASSISTANT_API_TOKEN", API_TOKEN, raising=False)
    monkeypatch.setattr(config, "MINIAPP_URL", "https://todo.example/")
    monkeypatch.setattr(config, "DB_PATH", str(Path(tempfile.mkdtemp()) / "mat.db"))
    loop.run_until_complete(storage.init_db())
    app = web.Application()
    bot = FakeBot()
    app["bot"] = bot
    routes.setup(app)
    c = TestClient(TestServer(app), loop=loop)
    loop.run_until_complete(c.start_server())
    yield SimpleNamespace(c=c, loop=loop, bot=bot, mp=monkeypatch)
    loop.run_until_complete(c.close())
    loop.run_until_complete(storage.close_db())


def bearer(token=API_TOKEN):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}


def req(env, method, path, headers=None, **kw):
    async def go():
        resp = await env.c.request(method, path, headers=headers, **kw)
        try:
            body = await resp.json()
        except Exception:
            body = None
        return resp.status, body
    return env.loop.run_until_complete(go())


def post(env, body=None, headers=None, **over):
    payload = {"title": "Заметка", "markdown": "# Привет\n\nТекст", "source": "personal/a.md", "idempotency_key": KEY}
    payload.update(over)
    return req(env, "POST", URL, bearer() if headers is None else headers, data=json.dumps(body if body is not None else payload, ensure_ascii=False).encode())


# --- вход с Mac ---

def test_публикация_создаёт_материал_и_шлёт_уведомление(env):
    status, body = post(env)
    assert status in (200, 201) and body["duplicate"] is False and isinstance(body["id"], int)
    (chat, text, markup), = env.bot.sent
    assert chat == OWNER and text == "📖 Новое: Заметка"
    assert markup.inline_keyboard[0][0].web_app.url == f"https://todo.example/?read={body['id']}"


def test_ответ_сообщает_было_ли_уведомление(env):
    _, first = post(env)
    assert first["notified"] is True
    _, again = post(env)
    assert again["notified"] is False
    env.bot.fail = True
    _, other = post(env, idempotency_key="f" * 32)
    assert other["duplicate"] is False and other["notified"] is False


def test_повтор_с_тем_же_ключом_тот_же_id_без_второго_уведомления(env):
    _, first = post(env)
    status, second = post(env)
    assert status == 200 and second == {"id": first["id"], "duplicate": True, "notified": False}
    assert len(env.bot.sent) == 1


def test_сбой_telegram_не_роняет_публикацию(env):
    env.bot.fail = True
    status, body = post(env)
    assert status in (200, 201) and body["duplicate"] is False


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Basic " + API_TOKEN},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer токен"},
])
def test_без_верного_токена_401(env, headers):
    status, _ = post(env, headers=headers)
    assert status == 401
    assert req(env, "GET", URL, headers=headers)[0] == 401
    assert req(env, "DELETE", URL + "/1", headers=headers)[0] == 401


def test_пустой_токен_выключает_эндпоинт(env):
    env.mp.setattr(config, "ASSISTANT_API_TOKEN", "")
    assert post(env, headers=bearer(""))[0] in (404, 503)
    assert post(env, headers={"Authorization": "Bearer "})[0] in (404, 503)
    assert req(env, "GET", URL, headers=bearer(""))[0] in (404, 503)


def test_токен_не_открывает_остальной_api(env):
    assert req(env, "GET", "/api/inbox", headers=bearer())[0] == 401
    assert req(env, "GET", "/api/reading", headers=bearer())[0] == 401


def test_initdata_не_открывает_вход_с_mac(env):
    assert req(env, "GET", URL, headers=_auth())[0] == 401


@pytest.mark.parametrize("over", [
    {"title": ""}, {"title": "   "}, {"title": "я" * 201}, {"title": 5},
    {"markdown": ""}, {"markdown": "  \n"}, {"markdown": None},
    {"source": "s" * 301}, {"source": 5},
    {"idempotency_key": "short"}, {"idempotency_key": "Z" * 32}, {"idempotency_key": None},
])
def test_валидация_400(env, over):
    assert post(env, **over)[0] == 400


def test_не_json_и_не_объект_400(env):
    assert req(env, "POST", URL, headers=bearer(), data=b"{oops")[0] == 400
    assert post(env, body=[1, 2])[0] == 400


def test_граничные_длины_проходят(env):
    assert post(env, title="я" * 200, source="s" * 300)[0] in (200, 201)


def test_лимит_тела_413(env):
    status, _ = post(env, markdown="x" * (256 * 1024 + 1))
    assert status == 413
    assert storage_count(env) == 0


def test_список_и_удаление_по_токену(env):
    _, made = post(env)
    status, body = req(env, "GET", URL, headers=bearer())
    assert status == 200
    item, = body["items"]
    assert item["id"] == made["id"] and item["title"] == "Заметка" and "markdown" not in item
    assert req(env, "DELETE", f"{URL}/{made['id']}", headers=bearer())[0] == 200
    assert req(env, "DELETE", f"{URL}/{made['id']}", headers=bearer())[0] == 404
    assert req(env, "GET", URL, headers=bearer())[1]["items"] == []


def test_в_лог_только_метод_путь_код(env, caplog):
    import logging
    caplog.set_level(logging.INFO)  # как в проде (basicConfig INFO)
    post(env, markdown="СЕКРЕТНЫЙ ТЕКСТ материала", title="СЕКРЕТНЫЙ ЗАГОЛОВОК")
    post(env, headers={"Authorization": "Bearer wrong-token-value"})
    text = caplog.text
    assert "СЕКРЕТНЫЙ" not in text and API_TOKEN not in text and "wrong-token-value" not in text
    assert "POST" in text and URL in text


def storage_count(env):
    async def go():
        async with storage._db.execute("SELECT COUNT(*) FROM materials") as c:
            return (await c.fetchone())[0]
    return env.loop.run_until_complete(go())


# --- чтение из мини-аппа ---

def tma(env, method, path, **kw):
    return req(env, method, path, headers=_auth(), **kw)


def test_мини_апп_без_подписи_не_читает(env):
    assert req(env, "GET", "/api/reading")[0] == 401
    assert req(env, "GET", "/api/reading", headers=_auth(user_id=1))[0] == 403


def test_список_порядок_и_счётчик(env):
    ids = [post(env, title=t, idempotency_key=f"{i:032x}")[1]["id"] for i, t in enumerate(["A", "B", "C"])]
    tma(env, "POST", f"/api/reading/{ids[1]}/read", json={"read": True})
    tma(env, "POST", f"/api/reading/{ids[0]}/pin", json={"pinned": True})
    status, body = tma(env, "GET", "/api/reading")
    assert status == 200 and body["unread"] == 2
    # закреплённое, затем непрочитанное (новее выше), затем прочитанное
    assert [i["title"] for i in body["items"]] == ["A", "C", "B"]
    assert body["items"][2]["read"] is True and body["items"][0]["pinned"] is True
    assert "markdown" not in body["items"][0]


def test_материал_целиком_и_404(env):
    _, made = post(env)
    status, body = tma(env, "GET", f"/api/reading/{made['id']}")
    assert status == 200 and body["markdown"].startswith("# Привет") and body["title"] == "Заметка"
    assert tma(env, "GET", "/api/reading/9999")[0] == 404
    assert tma(env, "GET", "/api/reading/abc")[0] == 404


def test_прочитано_можно_снять_и_удалить(env):
    _, made = post(env)
    mid = made["id"]
    tma(env, "POST", f"/api/reading/{mid}/read", json={"read": True})
    assert tma(env, "GET", "/api/reading")[1]["unread"] == 0
    tma(env, "POST", f"/api/reading/{mid}/read", json={"read": False})
    assert tma(env, "GET", "/api/reading")[1]["unread"] == 1
    assert tma(env, "DELETE", f"/api/reading/{mid}")[0] == 200
    assert tma(env, "GET", f"/api/reading/{mid}")[0] == 404


def test_сводка_содержит_счётчик_чтения(env):
    post(env)
    async def all_tasks(list_id=None):
        return []
    from services import ms_todo
    env.mp.setattr(ms_todo, "get_all_tasks", all_tasks)
    assert tma(env, "GET", "/api/summary")[1]["reading"] == 1


# --- миграция ---

def test_миграция_старой_бд_добавляет_таблицу(loop, monkeypatch):
    path = str(Path(tempfile.mkdtemp()) / "old.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
                "fire_at TEXT NOT NULL, text TEXT NOT NULL, task_id TEXT)")
    con.commit()
    con.close()
    loop.run_until_complete(storage.init_db())
    loop.run_until_complete(storage.close_db())
    loop.run_until_complete(storage.init_db())      # повторный старт
    mid, dup = loop.run_until_complete(storage.create_material("T", "md", "", KEY))
    assert not dup
    assert loop.run_until_complete(storage.create_material("T2", "md2", "", KEY)) == (mid, True)
    loop.run_until_complete(storage.close_db())


# --- ревью 2: безопасность ---

def test_не_ascii_и_суррогаты_в_заголовке_это_отказ_а_не_ошибка(monkeypatch):
    monkeypatch.setattr(config, "ASSISTANT_API_TOKEN", API_TOKEN)
    from api import material_routes
    assert material_routes._token_ok("Bearer \udcff\udcfe") is False
    assert material_routes._token_ok("Bearer токен") is False
    assert material_routes._token_ok("Bearer " + API_TOKEN) is True


def test_глубокий_json_это_400(env):
    status, _ = req(env, "POST", URL, headers=bearer(), data=b"[" * 100000 + b"]" * 100000)
    assert status == 400


@pytest.mark.parametrize("bad", ["1_0", "９", "9" * 30, "+1", "-1"])
def test_id_только_ascii_цифры(env, bad):
    _, made = post(env)
    assert tma(env, "GET", f"/api/reading/{bad}")[0] == 404
    assert req(env, "DELETE", f"{URL}/{bad}", headers=bearer())[0] == 404


def test_гонка_двух_публикаций_даёт_один_материал(env):
    import asyncio

    async def go():
        return await asyncio.gather(*(storage.create_material("T", "md", "", KEY) for _ in range(5)))

    results = env.loop.run_until_complete(go())
    assert len({r[0] for r in results}) == 1 and sum(1 for r in results if not r[1]) == 1
    assert storage_count(env) == 1


def test_путь_в_логе_экранирован(env, caplog):
    import logging
    caplog.set_level(logging.INFO)
    req(env, "DELETE", URL + "/1%0AFORGED%20LINE", headers=bearer())
    req(env, "POST", URL + "%0AFORGED2", headers={"Authorization": "Bearer x"}, data=b"{}")
    assert not any(line.startswith("FORGED") for line in caplog.text.splitlines())


def test_сбой_в_assistant_пути_не_маскируется_под_ms_todo(env):
    async def boom():
        raise RuntimeError("db down")

    env.mp.setattr(storage, "list_materials", boom)
    status, body = req(env, "GET", URL, headers=bearer())
    assert status == 500 and "MS To Do" not in json.dumps(body, ensure_ascii=False)


def test_токен_в_конфиге_короткий_это_проблема(monkeypatch):
    for value, bad in [("", False), ("x" * 31, True), ("x" * 32, False)]:
        monkeypatch.setattr(config, "ASSISTANT_API_TOKEN", value)
        assert any("ASSISTANT_API_TOKEN" in p for p in config.config_problems()) is bad
