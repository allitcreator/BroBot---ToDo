"""Перенос задачи вместе с её «временем».

У задачи в Graph времени нет — только дата. Время живёт в напоминании бота,
напоминании MS To Do и событии Google Calendar. При переносе они должны
переехать вместе с задачей, иначе напомнят в старый день.
"""
import asyncio
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import storage  # noqa: E402
from services import google_calendar, ms_todo, task_ops  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
DAY = date(2026, 10, 28)


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(config, "USER_TIMEZONE", "Europe/Moscow")
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "r.db")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(storage.init_db())
    yield loop.run_until_complete
    loop.run_until_complete(storage.close_db())
    loop.close()


@pytest.fixture
def graph(monkeypatch):
    """Подменённые Graph и Google: что записали — в calls."""
    calls = {"due": [], "ms_reminder": [], "removed": [], "event": [], "deleted_event": []}
    state = {"task": {"id": "t1", "title": "Позвонить"}, "event": None}

    async def update_task(task_id, title=None, due_date=None, list_id=None):
        calls["due"].append((task_id, due_date))

    async def get_task(task_id, list_id=None):
        return state["task"]

    async def set_reminder(task_id, fire_at_utc):
        calls["ms_reminder"].append((task_id, fire_at_utc))

    async def remove_reminder(task_id):
        calls["removed"].append(task_id)

    async def get_event(event_id):
        if state["event"] is None:
            req = httpx.Request("GET", "https://x")
            raise httpx.HTTPStatusError("gone", request=req, response=httpx.Response(404, request=req))
        return state["event"]

    async def update_event(event_id, start, end):
        calls["event"].append((event_id, start, end))

    async def delete_event(event_id):
        calls["deleted_event"].append(event_id)

    monkeypatch.setattr(ms_todo, "update_task", update_task)
    monkeypatch.setattr(ms_todo, "get_task", get_task)
    monkeypatch.setattr(ms_todo, "set_reminder", set_reminder)
    monkeypatch.setattr(ms_todo, "remove_reminder", remove_reminder)
    monkeypatch.setattr(google_calendar, "get_event", get_event)
    monkeypatch.setattr(google_calendar, "update_event", update_event)
    monkeypatch.setattr(google_calendar, "delete_event", delete_event)
    return calls, state


def _event(start: str, end: str) -> dict:
    return {"start": {"dateTime": start}, "end": {"dateTime": end}}


def _reminder_fire_at(run) -> list[str]:
    async def q():
        async with storage._db.execute("SELECT fire_at FROM reminders WHERE task_id = 't1'") as cur:
            return [r[0] for r in await cur.fetchall()]
    return run(q())


def test_без_напоминаний_только_дата(run, graph):
    calls, _ = graph
    warnings = run(task_ops.reschedule("t1", DAY, "16:00"))
    assert warnings == []
    assert calls["due"] == [("t1", "2026-10-28")]
    assert calls["ms_reminder"] == [] and calls["event"] == []


def test_напоминание_бота_переезжает_на_новое_время(run, graph):
    # Было: сегодня 15:00 МСК = 12:00 UTC.
    run(storage.save_reminder(1, "2026-10-27T12:00:00", "⏰", task_id="t1"))
    run(task_ops.reschedule("t1", DAY, "16:00"))
    assert _reminder_fire_at(run) == ["2026-10-28T13:00:00"]


def test_по_умолчанию_то_же_время_в_новый_день(run, graph):
    run(storage.save_reminder(1, "2026-10-27T12:00:00", "⏰", task_id="t1"))
    run(task_ops.reschedule("t1", DAY))
    assert _reminder_fire_at(run) == ["2026-10-28T12:00:00"]


def test_событие_и_напоминания_сдвигаются_с_тем_же_отступом(run, graph):
    calls, state = graph
    # Встреча 15:00–16:00, напоминание бота и MS To Do — за 15 минут.
    state["event"] = _event("2026-10-27T15:00:00+03:00", "2026-10-27T16:00:00+03:00")
    state["task"] = {
        "id": "t1", "title": "Позвонить", "isReminderOn": True,
        "reminderDateTime": {"dateTime": "2026-10-27T11:45:00.0000000", "timeZone": "UTC"},
    }
    run(storage.save_calendar_link("t1", "ev1"))
    run(storage.save_reminder(1, "2026-10-27T11:45:00", "⏰", task_id="t1"))

    assert run(task_ops.task_time("t1")) == "15:00"
    run(task_ops.reschedule("t1", DAY, "18:30"))

    ev_id, start, end = calls["event"][0]
    assert ev_id == "ev1"
    assert start == datetime(2026, 10, 28, 18, 30, tzinfo=MSK)
    assert end == datetime(2026, 10, 28, 19, 30, tzinfo=MSK)
    # 18:15 МСК = 15:15 UTC
    assert _reminder_fire_at(run) == ["2026-10-28T15:15:00"]
    assert calls["ms_reminder"] == [("t1", "2026-10-28T15:15:00")]


def test_без_времени_снимает_напоминания_и_событие(run, graph):
    calls, state = graph
    state["event"] = _event("2026-10-27T15:00:00+03:00", "2026-10-27T16:00:00+03:00")
    run(storage.save_calendar_link("t1", "ev1"))
    run(storage.save_reminder(1, "2026-10-27T12:00:00", "⏰", task_id="t1"))

    run(task_ops.reschedule("t1", DAY, None))

    assert calls["due"] == [("t1", "2026-10-28")]
    assert _reminder_fire_at(run) == []
    assert calls["deleted_event"] == ["ev1"]
    assert run(storage.get_calendar_link("t1")) is None


def test_удалённое_событие_не_мешает_напоминанию(run, graph):
    calls, state = graph
    state["event"] = None  # Google отвечает 404
    run(storage.save_calendar_link("t1", "ev1"))
    run(storage.save_reminder(1, "2026-10-27T12:00:00", "⏰", task_id="t1"))

    warnings = run(task_ops.reschedule("t1", DAY, "16:00"))

    assert warnings == []
    assert _reminder_fire_at(run) == ["2026-10-28T13:00:00"]
    assert run(storage.get_calendar_link("t1")) is None


def test_сбой_календаря_даёт_предупреждение_а_не_ошибку(run, graph, monkeypatch):
    calls, state = graph
    state["event"] = _event("2026-10-27T15:00:00+03:00", "2026-10-27T16:00:00+03:00")
    run(storage.save_calendar_link("t1", "ev1"))

    async def broken(*a):
        raise RuntimeError("google 500")

    monkeypatch.setattr(google_calendar, "update_event", broken)
    warnings = run(task_ops.reschedule("t1", DAY, "16:00"))
    assert calls["due"] == [("t1", "2026-10-28")]
    assert warnings == ["calendar"]


def test_время_задачи_без_напоминаний_пустое(run, graph):
    assert run(task_ops.task_time("t1")) is None


def test_время_из_напоминания_бота(run, graph):
    run(storage.save_reminder(1, "2026-10-27T12:00:00", "⏰", task_id="t1"))
    assert run(task_ops.task_time("t1")) == "15:00"


def test_перевод_часов_не_сдвигает_время(run, graph, monkeypatch):
    # Рига: 24.10 летнее время (UTC+3), 26.10 уже зимнее (UTC+2).
    monkeypatch.setattr(config, "USER_TIMEZONE", "Europe/Riga")
    run(storage.save_reminder(1, "2026-10-24T07:00:00", "⏰", task_id="t1"))  # 10:00 по Риге
    run(task_ops.reschedule("t1", date(2026, 10, 26)))
    assert _reminder_fire_at(run) == ["2026-10-26T08:00:00"]  # 10:00 по Риге


def test_сбой_чтения_напоминаний_после_переноса_даты_это_предупреждение(run, graph, monkeypatch):
    calls, _ = graph

    async def broken(task_id):
        raise RuntimeError("db locked")

    monkeypatch.setattr(storage, "get_task_reminders", broken)
    warnings = run(task_ops.reschedule("t1", DAY, "16:00"))
    assert calls["due"] == [("t1", "2026-10-28")]
    assert warnings == ["reminder"]


def test_массовый_перенос_не_обрывается_на_сбойной_задаче(run, graph, monkeypatch):
    calls, _ = graph
    tasks = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]

    async def today():
        return tasks

    real_update = ms_todo.update_task

    async def flaky(task_id, **kw):
        if task_id == "a":
            raise RuntimeError("graph 429")
        await real_update(task_id, **kw)

    monkeypatch.setattr(ms_todo, "get_tasks_today", today)
    monkeypatch.setattr(ms_todo, "update_task", flaky)
    moved = run(task_ops.move_today_to_tomorrow())
    assert [t["id"] for t in moved] == ["b"]
    assert [c[0] for c in calls["due"]] == ["b"]


def test_устаревшая_копия_задачи_не_прячет_напоминание_ms(run, graph):
    # Мини-апп передаёт задачу из кэша списка; там напоминания MS может не быть.
    calls, state = graph
    state["task"] = {
        "id": "t1", "title": "Позвонить", "isReminderOn": True,
        "reminderDateTime": {"dateTime": "2026-10-27T12:00:00.0000000", "timeZone": "UTC"},
    }
    run(storage.save_reminder(1, "2026-10-27T12:00:00", "⏰", task_id="t1"))
    run(task_ops.reschedule("t1", DAY, "16:00", task={"id": "t1", "title": "Позвонить"}))
    assert calls["ms_reminder"] == [("t1", "2026-10-28T13:00:00")]
