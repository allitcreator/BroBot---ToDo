"""API утреннего разбора: шаги, план дня, перенос выбранных задач."""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from api import ritual_routes  # noqa: E402
from db import storage  # noqa: E402
from services import google_calendar, ms_todo, rituals, task_ops  # noqa: E402
from test_api import OWNER, _auth, _register, client, loop  # noqa: E402,F401


def _due(day):
    return {"dateTime": f"{day.isoformat()}T00:00:00.0000000", "timeZone": "UTC"}


def _created(days_ago):
    d = datetime.combine(config.local_today(), datetime.min.time()) - timedelta(days=days_ago)
    return d.strftime("%Y-%m-%dT10:00:00Z")


@pytest.fixture
def tasks(monkeypatch):
    today = config.local_today()
    data = [
        {"id": "over", "title": "Просрочка", "createdDateTime": _created(3), "dueDateTime": _due(today - timedelta(days=1))},
        {"id": "stuck", "title": "Давняя", "createdDateTime": _created(40)},
        {"id": "inb", "title": "Свежая", "createdDateTime": _created(1), "body": {"content": "https://t.me/c/1/2"}},
        {"id": "tod", "title": "На сегодня", "createdDateTime": _created(1), "dueDateTime": _due(today)},
        {"id": "meet", "title": "Созвон", "createdDateTime": _created(1), "dueDateTime": _due(today)},
    ]

    async def all_tasks(list_id=None):
        return data

    monkeypatch.setattr(ms_todo, "get_all_tasks", all_tasks)
    ritual_routes._norm_cache.clear()
    return data


def _get(loop, client, path):
    resp = loop.run_until_complete(client.get(path, headers=_auth()))
    return resp.status, loop.run_until_complete(resp.json())


def _post(loop, client, path, body=None):
    resp = loop.run_until_complete(client.post(path, json=body, headers=_auth()))
    return resp.status, loop.run_until_complete(resp.json())


def test_шаги_утреннего_разбора(loop, client, tasks):
    status, data = _get(loop, client, "/api/ritual/morning")
    assert status == 200
    titles = {k: [t["title"] for t in data[k]] for k in ("overdue", "stuck", "inbox")}
    assert titles == {"overdue": ["Просрочка"], "stuck": ["Давняя"], "inbox": ["Свежая"]}
    assert data["inbox"][0]["note"] == "https://t.me/c/1/2"
    assert data["stuck"][0]["created"].startswith((config.local_today() - timedelta(days=40)).isoformat()[:4])
    assert "id" not in data["stuck"][0]


def test_порог_застряло_из_настроек(loop, client, tasks):
    loop.run_until_complete(storage.set_ritual_settings(OWNER, {"stuck_days": 30}))
    tasks[1]["createdDateTime"] = _created(20)
    _, data = _get(loop, client, "/api/ritual/morning")
    assert data["stuck"] == [] and [t["title"] for t in data["inbox"]] == ["Давняя", "Свежая"]


def test_план_дня_встречи_задачи_и_норма(loop, client, tasks, monkeypatch):
    loop.run_until_complete(storage.save_calendar_link("meet", "ev-task"))

    async def events_day(day):
        return [
            {"id": "ev-task", "summary": "Созвон", "start": {"dateTime": f"{day}T15:00:00+03:00"}},
            {"id": "ev-1", "summary": "Стоматолог", "start": {"dateTime": f"{day}T16:30:00+03:00"}},
            {"id": "ev-2", "summary": "Отпуск", "start": {"date": str(day)}},
        ]

    async def completed_since(day):
        # 30 дней: в три дня закрыто 2, 5 и 7 задач — медиана 5.
        base = config.local_today()
        days = [base - timedelta(days=1)] * 2 + [base - timedelta(days=2)] * 5 + [base - timedelta(days=3)] * 7
        return [{"completedDateTime": {"dateTime": f"{d.isoformat()}T09:00:00.0000000"}} for d in days]

    monkeypatch.setattr(google_calendar, "get_events_day", events_day)
    monkeypatch.setattr(ms_todo, "get_completed_since", completed_since)
    status, data = _get(loop, client, "/api/ritual/plan")
    assert status == 200
    assert data["events"] == [{"time": None, "title": "Отпуск"}, {"time": "16:30", "title": "Стоматолог"}]
    # Задача с событием — одна строка, со временем события; со временем — выше.
    assert [(t["title"], t.get("time")) for t in data["today"]] == [("Созвон", "15:00"), ("На сегодня", None)]
    assert data["norm"] == 5


def test_план_дня_без_календаря(loop, client, tasks, monkeypatch):
    async def broken(day):
        raise RuntimeError("google 500")

    async def completed_since(day):
        return []

    monkeypatch.setattr(google_calendar, "get_events_day", broken)
    monkeypatch.setattr(ms_todo, "get_completed_since", completed_since)
    status, data = _get(loop, client, "/api/ritual/plan")
    assert status == 200
    assert data["events"] is None and data["norm"] is None
    assert len(data["today"]) == 2


def test_ритуал_пройден(loop, client):
    status, _ = _post(loop, client, "/api/ritual/morning/done")
    assert status == 200
    run = loop.run_until_complete(storage.get_ritual_run("morning", config.local_today().isoformat()))
    assert run["done_at"] is not None
    status, _ = _post(loop, client, "/api/ritual/lunch/done")
    assert status == 404


def test_перенос_выбранных_задач(loop, client, tasks, monkeypatch):
    calls = []

    async def fake(task_id, day, new_time=task_ops.KEEP, task=None):
        calls.append((task_id, day.isoformat(), new_time))
        return []

    monkeypatch.setattr(task_ops, "reschedule", fake)
    k_over, k_stuck = _register(loop, "over"), _register(loop, "stuck")
    status, data = _post(loop, client, "/api/tasks/move", {"keys": [k_over, k_stuck], "to": "today", "times": {k_over: "10:00"}})
    assert status == 200 and data == {"moved": 2, "total": 2, "failed": []}
    today = config.local_today().isoformat()
    assert calls == [("over", today, "10:00"), ("stuck", today, task_ops.KEEP)]


def test_перенос_выбранных_на_дату_и_кривые_ключи(loop, client, tasks, monkeypatch):
    calls = []

    async def fake(task_id, day, new_time=task_ops.KEEP, task=None):
        calls.append((task_id, day.isoformat()))
        return []

    monkeypatch.setattr(task_ops, "reschedule", fake)
    key = _register(loop, "tod")
    status, _ = _post(loop, client, "/api/tasks/move", {"keys": [key], "to": "2026-12-31"})
    assert status == 200 and calls == [("tod", "2026-12-31")]
    status, _ = _post(loop, client, "/api/tasks/move", {"keys": ["deadbeef"], "to": "today"})
    assert status == 404
    status, _ = _post(loop, client, "/api/tasks/move", {"keys": "abc", "to": "today"})
    assert status == 400


def test_медиана_закрытий():
    assert rituals.median_per_day([3, 7, 5]) == 5
    assert rituals.median_per_day([2, 4]) == 3
    assert rituals.median_per_day([]) is None


def test_вечер_открытые_сделанные_и_завтра(loop, client, tasks, monkeypatch):
    today = config.local_today()
    tasks.append({"id": "tom", "title": "Забрать посылку", "createdDateTime": _created(1),
                  "dueDateTime": _due(today + timedelta(days=1))})

    async def completed_since(day):
        assert day == today
        return [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]

    async def events_day(day):
        assert day == today + timedelta(days=1)
        return [{"id": "e1", "summary": "Планёрка", "start": {"dateTime": f"{day}T10:00:00+03:00"}}]

    monkeypatch.setattr(ms_todo, "get_completed_since", completed_since)
    monkeypatch.setattr(google_calendar, "get_events_day", events_day)
    status, data = _get(loop, client, "/api/ritual/evening")
    assert status == 200
    assert [t["title"] for t in data["today_open"]] == ["На сегодня", "Созвон"]
    assert data["done_today"] == 3
    assert [t["title"] for t in data["tomorrow"]] == ["Забрать посылку"]
    assert data["tomorrow_events"] == [{"time": "10:00", "title": "Планёрка"}]
    assert data["inbox_count"] == 2  # «Свежая» и застрявшая «Давняя» — обе без даты
