"""API мини-аппа: подпись Telegram, доступ только владельцу, разбор входящих.

Мини-апп живёт по публичному адресу. Всё, что отделяет чужого человека от
задач владельца, — проверка подписи initData и сверка user.id. Поэтому
подделка в любом виде должна получать отказ.
"""
import asyncio
import hashlib
import hmac
import json
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from api import auth, routes  # noqa: E402
from db import storage  # noqa: E402
from services import ms_todo, task_ops  # noqa: E402

TOKEN = "123456:TEST-TOKEN"
OWNER = 4242


def make_init_data(*, token=TOKEN, user_id=OWNER, auth_date=None, **overrides) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF_test",
        "user": json.dumps({"id": user_id, "first_name": "A"}, separators=(",", ":")),
    }
    fields.update({k: str(v) for k, v in overrides.items()})
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# --- подпись ---

def test_подпись_принимается():
    assert auth.verify_init_data(make_init_data(), TOKEN)["user"]["id"] == OWNER


@pytest.mark.parametrize("init_data, reason", [
    ("", "пустая"),
    ("auth_date=1&user=%7B%7D", "подписи"),
])
def test_битые_данные_отклоняются(init_data, reason):
    with pytest.raises(auth.InitDataError, match=reason):
        auth.verify_init_data(init_data, TOKEN)


def test_чужой_токен_не_подходит():
    with pytest.raises(auth.InitDataError, match="не сходится"):
        auth.verify_init_data(make_init_data(token="999:OTHER"), TOKEN)


def test_подмена_пользователя_ломает_подпись():
    genuine = make_init_data()
    forged = genuine.replace(f"%22id%22%3A{OWNER}", "%22id%22%3A1")
    assert forged != genuine
    with pytest.raises(auth.InitDataError, match="не сходится"):
        auth.verify_init_data(forged, TOKEN)


def test_просроченная_подпись():
    with pytest.raises(auth.InitDataError, match="просрочена"):
        auth.verify_init_data(make_init_data(auth_date=int(time.time()) - 2 * 86400), TOKEN)


# --- HTTP ---

@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def client(loop, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER)
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "api.db")
    loop.run_until_complete(storage.init_db())

    app = web.Application()
    routes.setup(app)
    c = TestClient(TestServer(app), loop=loop)
    loop.run_until_complete(c.start_server())
    yield c
    loop.run_until_complete(c.close())
    loop.run_until_complete(storage.close_db())


def _auth(user_id=OWNER):
    return {"Authorization": f"tma {make_init_data(user_id=user_id)}"}


def test_health_без_авторизации(loop, client):
    resp = loop.run_until_complete(client.get("/health"))
    assert resp.status == 200


def test_без_initdata_401(loop, client):
    resp = loop.run_until_complete(client.get("/api/inbox"))
    assert resp.status == 401


def test_чужой_пользователь_403(loop, client):
    resp = loop.run_until_complete(client.get("/api/inbox", headers=_auth(user_id=1)))
    assert resp.status == 403


def test_список_входящих(loop, client, monkeypatch):
    async def fake_inbox():
        return [{
            "id": "ms-1", "title": "Статья про индексы",
            "body": {"content": "длинный текст " * 50},
            "createdDateTime": "2026-10-26T10:00:00Z",
        }]

    monkeypatch.setattr(ms_todo, "get_inbox_tasks", fake_inbox)
    resp = loop.run_until_complete(client.get("/api/inbox", headers=_auth()))
    assert resp.status == 200
    data = loop.run_until_complete(resp.json())
    assert data["count"] == 1
    item = data["items"][0]
    assert item["title"] == "Статья про индексы"
    assert len(item["note"]) <= 301
    assert "id" not in item, "наружу — короткий ключ, не id из Graph"
    assert loop.run_until_complete(storage.resolve_task_id(item["key"])) == "ms-1"


def _register(loop, full_id):
    return loop.run_until_complete(storage.register_task_id(full_id))


def test_разбор_на_сегодня_и_на_дату(loop, client, monkeypatch):
    calls = []

    async def fake_update(task_id, title=None, due_date=None, list_id=None):
        calls.append((task_id, due_date))

    monkeypatch.setattr(ms_todo, "update_task", fake_update)
    _open_tasks(monkeypatch, [{"id": "ms-2", "title": "Задача"}])
    key = _register(loop, "ms-2")

    for when, expected in [
        ("today", config.local_today().isoformat()),
        ("tomorrow", (config.local_today() + timedelta(days=1)).isoformat()),
        ("2026-12-31", "2026-12-31"),
    ]:
        resp = loop.run_until_complete(
            client.post(f"/api/tasks/{key}/schedule", json={"date": when}, headers=_auth())
        )
        assert resp.status == 200, when
        assert calls[-1] == ("ms-2", expected)


def test_кривая_дата_400(loop, client, monkeypatch):
    async def fake_update(*a, **kw):
        raise AssertionError("не должно дойти до Graph")

    monkeypatch.setattr(ms_todo, "update_task", fake_update)
    key = _register(loop, "ms-3")
    resp = loop.run_until_complete(
        client.post(f"/api/tasks/{key}/schedule", json={"date": "послезавтра"}, headers=_auth())
    )
    assert resp.status == 400


def test_неизвестный_ключ_404(loop, client):
    resp = loop.run_until_complete(
        client.post("/api/tasks/deadbeef/schedule", json={"date": "today"}, headers=_auth())
    )
    assert resp.status == 404


def test_удаление_чистит_хвосты(loop, client, monkeypatch):
    deleted, cleaned = [], []

    async def fake_delete(task_id, list_id=None):
        deleted.append(task_id)

    async def fake_cleanup(task_id):
        cleaned.append(task_id)

    monkeypatch.setattr(ms_todo, "delete_task", fake_delete)
    monkeypatch.setattr(task_ops, "cleanup_task_extras", fake_cleanup)
    key = _register(loop, "ms-4")

    resp = loop.run_until_complete(client.delete(f"/api/tasks/{key}", headers=_auth()))
    assert resp.status == 200
    assert deleted == ["ms-4"] and cleaned == ["ms-4"]


def test_ошибка_graph_отдаёт_502_без_подробностей(loop, client, monkeypatch):
    async def broken():
        raise Exception("MS Graph 500: {secret internals}")

    monkeypatch.setattr(ms_todo, "get_inbox_tasks", broken)
    resp = loop.run_until_complete(client.get("/api/inbox", headers=_auth()))
    assert resp.status == 502
    assert "internals" not in loop.run_until_complete(resp.text())


@pytest.mark.parametrize("bad_hash", ["%D0%B0", "zz", "A" * 64])
def test_кривой_формат_подписи_это_отказ_а_не_падение(bad_hash):
    init_data = f"auth_date={int(time.time())}&user=%7B%22id%22%3A1%7D&hash={bad_hash}"
    with pytest.raises(auth.InitDataError):
        auth.verify_init_data(init_data, TOKEN)


def test_подпись_живёт_не_больше_двух_часов():
    assert auth.MAX_AUTH_AGE_SECONDS <= 2 * 60 * 60


# --- «Сегодня», «Неделя», счётчики ---

def _due(day):
    """Срок, как его отдаёт Graph: только дата, полночь UTC."""
    return {"dateTime": f"{day.isoformat()}T00:00:00.0000000", "timeZone": "UTC"}


def _open_tasks(monkeypatch, tasks):
    async def fake_all(list_id=None):
        return tasks

    monkeypatch.setattr(ms_todo, "get_all_tasks", fake_all)


def _sample(today):
    return [
        {"id": "over", "title": "Просрочка", "dueDateTime": _due(today - timedelta(days=2))},
        {"id": "tod", "title": "Сегодняшняя", "dueDateTime": _due(today)},
        {"id": "tom", "title": "Завтрашняя", "dueDateTime": _due(today + timedelta(days=1))},
        {"id": "far", "title": "Через месяц", "dueDateTime": _due(today + timedelta(days=30))},
        {"id": "inb", "title": "Во входящих"},
    ]


def test_сегодня_отдельно_от_просроченных(loop, client, monkeypatch):
    today = config.local_today()
    _open_tasks(monkeypatch, _sample(today))
    resp = loop.run_until_complete(client.get("/api/tasks?view=today", headers=_auth()))
    assert resp.status == 200
    data = loop.run_until_complete(resp.json())
    assert [t["title"] for t in data["overdue"]] == ["Просрочка"]
    assert [t["title"] for t in data["today"]] == ["Сегодняшняя"]
    assert data["overdue"][0]["date"] == (today - timedelta(days=2)).isoformat()


def test_неделя_по_дням(loop, client, monkeypatch):
    today = config.local_today()
    _open_tasks(monkeypatch, _sample(today))
    resp = loop.run_until_complete(client.get("/api/tasks?view=week", headers=_auth()))
    data = loop.run_until_complete(resp.json())
    days = data["days"]
    assert [d["date"] for d in days] == [(today + timedelta(days=i)).isoformat() for i in range(7)]
    assert [t["title"] for t in days[0]["items"]] == ["Сегодняшняя"]
    assert [t["title"] for t in days[1]["items"]] == ["Завтрашняя"]
    assert all("Через месяц" not in [t["title"] for t in d["items"]] for d in days)


def test_значки_напоминания_и_календаря(loop, client, monkeypatch):
    today = config.local_today()
    _open_tasks(monkeypatch, [{"id": "tod", "title": "С напоминанием", "dueDateTime": _due(today)}])
    loop.run_until_complete(storage.save_calendar_link("tod", "ev-1"))
    resp = loop.run_until_complete(client.get("/api/tasks?view=today", headers=_auth()))
    item = loop.run_until_complete(resp.json())["today"][0]
    assert item["calendar"] is True
    assert item["reminder"] is False


def test_неизвестный_срез_400(loop, client):
    resp = loop.run_until_complete(client.get("/api/tasks?view=month", headers=_auth()))
    assert resp.status == 400


def test_счётчики_для_вкладок(loop, client, monkeypatch):
    _open_tasks(monkeypatch, _sample(config.local_today()))
    resp = loop.run_until_complete(client.get("/api/summary", headers=_auth()))
    assert loop.run_until_complete(resp.json()) == {"inbox": 1, "today": 1, "overdue": 1}


def test_выполнено_чистит_хвосты(loop, client, monkeypatch):
    done, cleaned = [], []

    async def fake_complete(task_id, list_id=None):
        done.append(task_id)

    async def fake_cleanup(task_id):
        cleaned.append(task_id)

    monkeypatch.setattr(ms_todo, "complete_task", fake_complete)
    monkeypatch.setattr(task_ops, "cleanup_task_extras", fake_cleanup)
    key = _register(loop, "ms-done")
    resp = loop.run_until_complete(client.post(f"/api/tasks/{key}/complete", headers=_auth()))
    assert resp.status == 200
    assert done == ["ms-done"] and cleaned == ["ms-done"]


def test_массовый_перенос_просроченных_с_итогом(loop, client, monkeypatch):
    today = config.local_today()
    _open_tasks(monkeypatch, [
        {"id": "o1", "title": "Первая", "dueDateTime": _due(today - timedelta(days=1))},
        {"id": "o2", "title": "Вторая", "dueDateTime": _due(today - timedelta(days=3))},
        {"id": "t1", "title": "Сегодняшняя", "dueDateTime": _due(today)},
    ])
    moved = []

    async def fake_update(task_id, title=None, due_date=None, list_id=None):
        if task_id == "o2":
            raise Exception("MS Graph 429")
        moved.append((task_id, due_date))

    monkeypatch.setattr(ms_todo, "update_task", fake_update)
    resp = loop.run_until_complete(
        client.post("/api/tasks/move", json={"scope": "overdue", "to": "today"}, headers=_auth())
    )
    data = loop.run_until_complete(resp.json())
    assert moved == [("o1", today.isoformat())]
    assert data == {"moved": 1, "total": 2, "failed": ["Вторая"]}


def test_массовый_перенос_сегодняшних_на_завтра(loop, client, monkeypatch):
    today = config.local_today()
    _open_tasks(monkeypatch, _sample(today))
    moved = []

    async def fake_update(task_id, title=None, due_date=None, list_id=None):
        moved.append((task_id, due_date))

    monkeypatch.setattr(ms_todo, "update_task", fake_update)
    resp = loop.run_until_complete(
        client.post("/api/tasks/move", json={"scope": "today", "to": "tomorrow"}, headers=_auth())
    )
    assert loop.run_until_complete(resp.json())["moved"] == 1
    assert moved == [("tod", (today + timedelta(days=1)).isoformat())]


def test_массовый_перенос_кривые_параметры(loop, client):
    resp = loop.run_until_complete(
        client.post("/api/tasks/move", json={"scope": "all", "to": "today"}, headers=_auth())
    )
    assert resp.status == 400


# --- перенос со временем ---

def _fake_reschedule(monkeypatch, warnings=None):
    calls = []

    async def fake(task_id, day, new_time=task_ops.KEEP, task=None):
        calls.append((task_id, day.isoformat(), new_time))
        return warnings or []

    monkeypatch.setattr(task_ops, "reschedule", fake)
    return calls


def test_перенос_с_новым_временем(loop, client, monkeypatch):
    _open_tasks(monkeypatch, [{"id": "ms-5", "title": "Позвонить"}])
    calls = _fake_reschedule(monkeypatch)
    key = _register(loop, "ms-5")
    for body, expected in [
        ({"date": "2026-12-31", "time": "16:00"}, "16:00"),
        ({"date": "2026-12-31", "time": None}, None),
        ({"date": "2026-12-31"}, task_ops.KEEP),
    ]:
        resp = loop.run_until_complete(client.post(f"/api/tasks/{key}/schedule", json=body, headers=_auth()))
        assert resp.status == 200, body
        assert calls[-1] == ("ms-5", "2026-12-31", expected)


@pytest.mark.parametrize("bad", ["25:00", "16-00", "утром", 16])
def test_кривое_время_400(loop, client, monkeypatch, bad):
    _fake_reschedule(monkeypatch)
    key = _register(loop, "ms-6")
    resp = loop.run_until_complete(
        client.post(f"/api/tasks/{key}/schedule", json={"date": "today", "time": bad}, headers=_auth())
    )
    assert resp.status == 400


def test_перенос_сообщает_о_сбое_календаря(loop, client, monkeypatch):
    _open_tasks(monkeypatch, [])
    _fake_reschedule(monkeypatch, warnings=["calendar"])
    key = _register(loop, "ms-7")
    resp = loop.run_until_complete(
        client.post(f"/api/tasks/{key}/schedule", json={"date": "today", "time": "10:00"}, headers=_auth())
    )
    data = loop.run_until_complete(resp.json())
    assert data["warnings"] == ["calendar"]


def test_массовый_перенос_с_временем_по_задачам(loop, client, monkeypatch):
    today = config.local_today()
    _open_tasks(monkeypatch, _sample(today))
    calls = _fake_reschedule(monkeypatch)
    key_tod = _register(loop, "tod")
    resp = loop.run_until_complete(client.post(
        "/api/tasks/move",
        json={"scope": "today", "to": "tomorrow", "times": {key_tod: "09:00"}},
        headers=_auth(),
    ))
    assert loop.run_until_complete(resp.json())["moved"] == 1
    assert calls == [("tod", (today + timedelta(days=1)).isoformat(), "09:00")]


def test_время_задачи(loop, client, monkeypatch):
    _open_tasks(monkeypatch, [{"id": "ms-8", "title": "Позвонить"}])

    async def fake_time(task_id, task=None):
        assert task == {"id": "ms-8", "title": "Позвонить"}, "задача — из кэша, без лишнего запроса"
        return "15:00"

    monkeypatch.setattr(task_ops, "task_time", fake_time)
    key = _register(loop, "ms-8")
    resp = loop.run_until_complete(client.get(f"/api/tasks/{key}/time", headers=_auth()))
    assert loop.run_until_complete(resp.json()) == {"time": "15:00"}
