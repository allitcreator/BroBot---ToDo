"""API мини-аппа, этап D: настройки, статистика, проекты.

Проекты пишут бриф целиком (прочитать заметку → поменять секцию → записать
всё), поэтому параллельные «Дополнить» и «Разведать» обязаны идти по
очереди — иначе одна запись затирает другую.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from api import routes  # noqa: E402
from db import storage  # noqa: E402
from services import ms_todo, projects, research  # noqa: E402
from test_api import OWNER, _auth, _register, client, loop  # noqa: E402,F401


def _get(loop, client, path):
    resp = loop.run_until_complete(client.get(path, headers=_auth()))
    return resp.status, loop.run_until_complete(resp.json())


def _send(loop, client, method, path, body=None):
    resp = loop.run_until_complete(client.request(method, path, json=body, headers=_auth()))
    return resp.status, loop.run_until_complete(resp.json())


# --- настройки ---

def test_настройки_по_умолчанию(loop, client):
    status, data = _get(loop, client, "/api/settings")
    assert status == 200
    assert data == {
        "confirm_mode": "all", "research_enabled": True, "projects_enabled": True,
        "morning_enabled": True, "morning_time": "09:30",
        "evening_enabled": True, "evening_time": "20:30",
        "stuck_days": 14, "weekend_rituals": False,
    }


def test_настройки_меняются_и_видны_чату(loop, client):
    status, data = _send(loop, client, "PATCH", "/api/settings", {"confirm_mode": "off", "research_enabled": False})
    assert status == 200
    assert data["confirm_mode"] == "off" and data["research_enabled"] is False
    assert loop.run_until_complete(storage.get_confirm_mode(OWNER)) == "off"
    assert loop.run_until_complete(storage.get_research_enabled(OWNER)) is False
    assert loop.run_until_complete(storage.get_projects_enabled(OWNER)) is True


def test_настройки_ритуалов_меняются_и_переживают_рестарт(loop, client):
    changes = {"morning_time": "08:00", "evening_enabled": False, "stuck_days": 7, "weekend_rituals": True}
    status, data = _send(loop, client, "PATCH", "/api/settings", changes)
    assert status == 200
    assert {k: data[k] for k in changes} == changes
    assert data["morning_enabled"] is True and data["evening_time"] == "20:30"

    loop.run_until_complete(storage.close_db())
    loop.run_until_complete(storage.init_db())
    status, data = _get(loop, client, "/api/settings")
    assert {k: data[k] for k in changes} == changes


def test_ритуалы_читаются_одним_словарём(loop, client):
    _send(loop, client, "PATCH", "/api/settings", {"evening_time": "21:00"})
    rituals = loop.run_until_complete(storage.get_ritual_settings(OWNER))
    assert rituals["evening_time"] == "21:00" and rituals["morning_time"] == "09:30"


@pytest.mark.parametrize("body", [{"confirm_mode": "sometimes"}, {"research_enabled": "yes"}, {"color": "red"}, {},
    {"morning_time": "9:30"}, {"morning_time": "09:15"}, {"morning_time": "０９:30"}, {"morning_time": "²3:00"}, {"evening_time": "24:00"},
    {"stuck_days": 10}, {"stuck_days": "14"}, {"morning_enabled": 1}, {"weekend_rituals": None}])
def test_кривые_настройки_400(loop, client, body):
    status, _ = _send(loop, client, "PATCH", "/api/settings", body)
    assert status == 400


# --- статистика ---

def test_статистика_не_читает_всю_историю(monkeypatch):
    """1500+ завершённых — 16 страниц и 27 с. Завершённые берём фильтром по дате."""
    filters = []

    async def fake_get_tasks(odata_filter=None, list_id=None):
        filters.append(odata_filter)
        return []

    monkeypatch.setattr(ms_todo, "get_tasks", fake_get_tasks)
    asyncio.run(ms_todo.get_stats())
    assert "status eq 'completed'" not in filters
    assert any("completedDateTime" in (f or "") for f in filters)


def test_статистика_по_дням(monkeypatch):
    today = config.local_today()

    def done(day_offset):
        d = today.toordinal() - day_offset
        from datetime import date
        return {"completedDateTime": {"dateTime": f"{date.fromordinal(d).isoformat()}T10:00:00.0000000", "timeZone": "UTC"},
                "createdDateTime": "2020-01-01T00:00:00Z"}

    async def fake_get_tasks(odata_filter=None, list_id=None):
        if "completedDateTime" in (odata_filter or ""):
            return [done(0), done(0), done(1), done(6)]
        if "createdDateTime" in (odata_filter or ""):
            return [{"createdDateTime": f"{today.isoformat()}T09:00:00Z"}]
        return [{"id": "a"}, {"id": "b", "dueDateTime": {"dateTime": "2000-01-01T00:00:00", "timeZone": "UTC"}}]

    monkeypatch.setattr(ms_todo, "get_tasks", fake_get_tasks)
    s = asyncio.run(ms_todo.get_stats())
    assert s["completed_today"] == 2
    assert s["created_today"] == 1
    assert s["open_tasks"] == 2 and s["overdue"] == 1 and s["inbox"] == 1
    assert [d["completed"] for d in s["week"]] == [1, 0, 0, 0, 0, 1, 2]


def test_статистика_в_api(loop, client, monkeypatch):
    async def fake_stats():
        return {"completed_today": 1, "created_today": 2, "open_tasks": 3, "overdue": 0, "inbox": 1, "week": []}

    monkeypatch.setattr(ms_todo, "get_stats", fake_stats)
    status, data = _get(loop, client, "/api/stats")
    assert status == 200 and data["open_tasks"] == 3


# --- проекты ---

BODY = projects.render_body({"why": "Устал терять заявки\nвторая строка", "what": "Бот"})


@pytest.fixture
def project(loop, monkeypatch):
    monkeypatch.setattr(config, "MS_PROJECTS_LIST_ID", "proj-list")

    async def fake_list():
        return [{"id": "p1", "title": "TeamNeedsBot", "body": {"content": BODY}, "createdDateTime": "2026-10-01T00:00:00Z"}]

    async def fake_get(task_id):
        return {
            "task": {"id": task_id},
            "brief": {**projects.parse_body(BODY), "title": "TeamNeedsBot"},
            "body_valid": True,
            "steps": [{"id": "s1", "text": "Сделать MVP", "checked": False}],
        }

    monkeypatch.setattr(projects, "list_projects", fake_list)
    monkeypatch.setattr(projects, "get_project", fake_get)
    return _register(loop, "p1")


def test_проекты_выключены_403(loop, client, project):
    loop.run_until_complete(storage.set_projects_enabled(OWNER, False))
    status, _ = _get(loop, client, "/api/projects")
    assert status == 403
    loop.run_until_complete(storage.set_projects_enabled(OWNER, True))


def test_список_проектов(loop, client, project):
    status, data = _get(loop, client, "/api/projects")
    assert status == 200
    assert data["items"] == [{"key": project, "title": "TeamNeedsBot", "summary": "Устал терять заявки"}]


def test_карточка_проекта(loop, client, project):
    status, data = _get(loop, client, f"/api/projects/{project}")
    assert status == 200
    assert data["title"] == "TeamNeedsBot"
    why = next(s for s in data["sections"] if s["key"] == "why")
    assert why["title"] == "Зачем" and why["value"].startswith("Устал")
    assert data["steps"] == [{"id": "s1", "text": "Сделать MVP", "checked": False}]
    assert data["research_enabled"] is True


def test_отметка_шага(loop, client, project, monkeypatch):
    calls = []

    async def fake_update(task_id, item_id, checked, list_id=None):
        calls.append((task_id, item_id, checked, list_id))

    monkeypatch.setattr(ms_todo, "update_checklist_item", fake_update)
    status, _ = _send(loop, client, "PATCH", f"/api/projects/{project}/steps/s1", {"checked": True})
    assert status == 200
    assert calls == [("p1", "s1", True, "proj-list")]


def test_дополнить_секцию(loop, client, project, monkeypatch):
    calls = []

    async def fake_append(task_id, key, text):
        calls.append((task_id, key, text))
        return {}

    monkeypatch.setattr(projects, "append_to_section", fake_append)
    status, _ = _send(loop, client, "POST", f"/api/projects/{project}/sections/risks", {"text": "Не взлетит"})
    assert status == 200
    assert calls == [("p1", "risks", "Не взлетит")]


def test_дополнить_неизвестную_секцию_400(loop, client, project):
    status, _ = _send(loop, client, "POST", f"/api/projects/{project}/sections/colors", {"text": "x"})
    assert status == 400


def test_правленную_руками_заметку_не_перезаписываем(loop, client, project, monkeypatch):
    async def broken_get(task_id):
        return {"task": {}, "brief": {}, "body_valid": False, "steps": []}

    async def must_not_append(*a):
        raise AssertionError("заметку правили руками — шаблоном не пересобираем")

    monkeypatch.setattr(projects, "get_project", broken_get)
    monkeypatch.setattr(projects, "append_to_section", must_not_append)
    status, data = _send(loop, client, "POST", f"/api/projects/{project}/sections/risks", {"text": "x"})
    assert status == 409


def test_параллельные_записи_брифа_по_очереди(loop, client, project, monkeypatch):
    active, overlaps = [0], []

    async def slow_append(task_id, key, text):
        active[0] += 1
        overlaps.append(active[0])
        await asyncio.sleep(0.02)
        active[0] -= 1
        return {}

    monkeypatch.setattr(projects, "append_to_section", slow_append)

    async def both():
        return await asyncio.gather(*(
            client.post(f"/api/projects/{project}/sections/risks", json={"text": str(i)}, headers=_auth())
            for i in range(3)
        ))

    loop.run_until_complete(both())
    assert max(overlaps) == 1


def test_разведка_в_фоне_со_статусом(loop, client, project, monkeypatch):
    gate = asyncio.Event()
    written = []

    async def fake_research(title, brief):
        await gate.wait()
        return {"summary": "Есть готовое", "sources": [], "error": None}

    async def fake_set(task_id, key, value):
        written.append((task_id, key))
        return {}

    monkeypatch.setattr(research, "research", fake_research)
    monkeypatch.setattr(projects, "set_section", fake_set)

    status, data = _send(loop, client, "POST", f"/api/projects/{project}/research")
    assert status == 202 and data["status"] == "running"
    _, data = _get(loop, client, f"/api/projects/{project}/research")
    assert data["status"] == "running"

    gate.set()

    async def settle():
        for _ in range(50):
            await asyncio.sleep(0.01)
            if written:
                break
        await asyncio.sleep(0.01)

    loop.run_until_complete(settle())
    _, data = _get(loop, client, f"/api/projects/{project}/research")
    assert data["status"] == "done"
    assert written == [("p1", "research")]


def test_разведка_выключена_403(loop, client, project):
    loop.run_until_complete(storage.set_research_enabled(OWNER, False))
    status, _ = _send(loop, client, "POST", f"/api/projects/{project}/research")
    assert status == 403
    loop.run_until_complete(storage.set_research_enabled(OWNER, True))
