"""Входящие: всё без даты ложится складом, а не на сегодня.

Раньше бот подставлял сегодняшнюю дату любому сообщению, и «посмотреть
фильм» вставало в план дня. Теперь нет срока — задача без dueDateTime,
её разбирают позже в мини-аппе. Дату задают в самом сообщении или
комментарием к форварду.
"""
import asyncio
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import storage  # noqa: E402
from handlers import callbacks, commands, messages, utils  # noqa: E402
from services import llm, ms_todo  # noqa: E402

USER = 779


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def setup_module():
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "test.db")
    asyncio.set_event_loop(asyncio.new_event_loop())
    run(storage.init_db())


def teardown_module():
    run(storage.close_db())


class FakeMessage:
    def __init__(self, text=None, forward_origin=None):
        self.from_user = SimpleNamespace(id=USER)
        self.chat = SimpleNamespace(id=42)
        self.message_id = 1
        self.text = text
        self.caption = None
        self.voice = None
        self.forward_origin = forward_origin
        self.answers = []
        self.markups = []
        self.edits = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)
        self.markups.append(reply_markup)

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)

    async def edit_reply_markup(self, reply_markup=None):
        pass


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=USER)
        self.message = FakeMessage()
        self.bot = None
        self.answers = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)


def _fake_llm(monkeypatch, payload):
    class _Completions:
        async def create(self, **kwargs):
            msg = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(llm.client, "chat", SimpleNamespace(completions=_Completions()))


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# --- разбор текста ---

def test_без_даты_задача_остаётся_без_даты(monkeypatch):
    _fake_llm(monkeypatch, {"title": "посмотреть фильм", "due_date": None, "due_time": None})
    task = run(llm.parse_task("посмотреть фильм", today="2026-10-26"))
    assert task["due_date"] is None


def test_время_без_даты_значит_сегодня(monkeypatch):
    _fake_llm(monkeypatch, {"title": "позвонить", "due_date": None, "due_time": "15:00"})
    task = run(llm.parse_task("в 15:00 позвонить", today="2026-10-26"))
    assert task["due_date"] == "2026-10-26"


def test_явная_дата_сохраняется(monkeypatch):
    _fake_llm(monkeypatch, {"title": "позвонить", "due_date": "2026-10-27", "due_time": None})
    task = run(llm.parse_task("завтра позвонить", today="2026-10-26"))
    assert task["due_date"] == "2026-10-27"


def test_превью_без_даты_показывает_входящие():
    preview = utils.format_task_preview({"title": "посмотреть фильм", "due_date": None})
    assert "Во входящие" in preview


# --- срез входящих ---

def test_входящие_это_открытые_задачи_без_срока(monkeypatch):
    async def fake_get_tasks(odata_filter=None, list_id=None):
        assert "completed" in (odata_filter or "")
        return [
            {"id": "a", "title": "без даты"},
            {"id": "b", "title": "с датой", "dueDateTime": {"dateTime": "2026-10-26T20:00:00", "timeZone": "UTC"}},
        ]

    monkeypatch.setattr(ms_todo, "get_tasks", fake_get_tasks)
    assert [t["id"] for t in run(ms_todo.get_inbox_tasks())] == ["a"]


# --- создание из чата ---

def _capture_create(monkeypatch):
    created = []

    async def fake_create(title, due_date=None, due_time=None, subtasks=None, description=None, list_id=None):
        created.append({"title": title, "due_date": due_date, "description": description})
        return {"id": f"ms-id-{len(created)}"}

    async def fake_inbox():
        return [{"id": "x"}] * 3

    monkeypatch.setattr(ms_todo, "create_task", fake_create)
    monkeypatch.setattr(ms_todo, "get_inbox_tasks", fake_inbox)
    return created


def test_задача_без_даты_уходит_во_входящие_без_вопросов(monkeypatch):
    created = _capture_create(monkeypatch)
    msg = FakeMessage("посмотреть фильм")
    task = {"title": "посмотреть фильм", "due_date": None, "is_event": True}

    run(messages._create_task_and_ask_calendar(msg, task))

    assert created == [{"title": "посмотреть фильм", "due_date": None, "description": None}]
    assert len(msg.answers) == 1, "ни календаря, ни напоминания у входящих"
    assert "Во входящих" in msg.answers[0] and "3" in msg.answers[0]
    buttons = _buttons(msg.markups[0])
    assert any(b.startswith("inbox:today:") for b in buttons)
    assert any(b.startswith("inbox:tomorrow:") for b in buttons)


def test_подтверждённая_задача_без_даты_тоже_во_входящие(monkeypatch):
    created = _capture_create(monkeypatch)
    run(storage.save_pending_task(USER, {"title": "почитать про sqlite", "due_date": None}))
    cb = FakeCallback("confirm:create")

    run(callbacks.cb_confirm_create(cb))

    assert created[0]["due_date"] is None
    assert "Во входящих" in cb.message.answers[0]


def test_форвард_без_комментария_сразу_во_входящие(monkeypatch):
    created = _capture_create(monkeypatch)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    origin = SimpleNamespace(type="user", chat=None)
    msg = FakeMessage("Статья про индексы в Postgres\nи ещё много текста", forward_origin=origin)

    run(messages._handle_new_task(msg))

    assert len(created) == 1
    assert created[0]["due_date"] is None
    assert created[0]["title"] == "Статья про индексы в Postgres"
    assert "индексы" in created[0]["description"]
    assert not any("Напиши что сделать" in a for a in msg.answers)
    assert run(storage.pop_forward(USER)) is None, "форвард не должен висеть в БД"


def test_форвард_без_текста_получает_заглушку_в_названии(monkeypatch):
    created = _capture_create(monkeypatch)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    msg = FakeMessage(None, forward_origin=SimpleNamespace(type="user", chat=None))

    run(messages._handle_new_task(msg))

    assert created[0]["title"] == "Пересланное сообщение"


def test_skip_чистит_сохранённый_форвард():
    run(storage.save_forward(USER, "чужой пост"))
    msg = FakeMessage("/skip")

    run(commands.cmd_skip(msg))

    assert run(storage.pop_forward(USER)) is None


# --- разбор кнопками в чате ---

def _capture_update(monkeypatch):
    updates = []

    async def fake_update(task_id, title=None, due_date=None, list_id=None):
        updates.append((task_id, due_date))

    monkeypatch.setattr(ms_todo, "update_task", fake_update)
    return updates


def test_кнопка_сегодня_ставит_сегодняшнюю_дату(monkeypatch):
    updates = _capture_update(monkeypatch)
    key = run(storage.register_task_id("ms-inbox-1"))
    cb = FakeCallback(f"inbox:today:{key}")

    run(callbacks.cb_inbox_schedule(cb))

    assert updates == [("ms-inbox-1", config.local_today().isoformat())]
    assert cb.message.edits, "сообщение должно показать новый срок"


def test_кнопка_завтра_ставит_завтрашнюю_дату(monkeypatch):
    updates = _capture_update(monkeypatch)
    key = run(storage.register_task_id("ms-inbox-2"))
    cb = FakeCallback(f"inbox:tomorrow:{key}")

    run(callbacks.cb_inbox_schedule(cb))

    assert updates == [("ms-inbox-2", (config.local_today() + timedelta(days=1)).isoformat())]
