"""Входящие — это пересланное.

Написанное руками — задача: дата из текста, без даты — сегодня. Форвард
без комментария или с комментарием без даты ложится во входящие и
разбирается в мини-аппе. Комментарий с датой («посмотреть сегодня») —
обычная задача на эту дату, пост уходит в заметку.
"""
import asyncio
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

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


class _FakeBot:
    async def send_chat_action(self, *a, **kw):
        pass


class FakeMessage:
    def __init__(self, text=None, forward_origin=None):
        self.from_user = SimpleNamespace(id=USER)
        self.chat = SimpleNamespace(id=42)
        self.message_id = 1
        self.text = text
        self.caption = None
        self.voice = None
        self.forward_origin = forward_origin
        self.bot = _FakeBot()
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


# --- разбор текста, написанного руками ---

def test_без_даты_задача_на_сегодня(monkeypatch):
    _fake_llm(monkeypatch, {"title": "купить молоко", "due_date": None, "due_time": None})
    task = run(llm.parse_task("купить молоко", today="2026-10-26"))
    assert task["due_date"] == "2026-10-26"


def test_для_комментария_дата_не_подставляется(monkeypatch):
    _fake_llm(monkeypatch, {"title": "глянуть", "due_date": None, "due_time": None})
    task = run(llm.parse_task("глянуть", today="2026-10-26", default_today=False))
    assert task["due_date"] is None


def test_явная_дата_сохраняется(monkeypatch):
    _fake_llm(monkeypatch, {"title": "позвонить", "due_date": "2026-10-27", "due_time": None})
    task = run(llm.parse_task("завтра позвонить", today="2026-10-26"))
    assert task["due_date"] == "2026-10-27"


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


# --- поток сообщений ---

@pytest.fixture
def fast(monkeypatch):
    """Короткие окна ожидания, чтобы тесты не спали секундами."""
    monkeypatch.setattr(messages, "COMMENT_WAIT", 0.05)
    monkeypatch.setattr(messages, "FORWARD_SETTLE", 0.15)


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


def _no_llm(monkeypatch):
    async def boom(*a, **kw):
        raise AssertionError("для форварда без комментария LLM не нужен")

    monkeypatch.setattr(llm, "classify_message", boom)
    monkeypatch.setattr(llm, "parse_task", boom)


def _comment_parse(monkeypatch, due_date=None, title=None, fail=False):
    """parse_task для комментария: возвращает дату или None, классификатор не нужен."""
    seen = []

    async def fake_parse(text, today=None, default_today=True):
        seen.append(default_today)
        if fail:
            raise RuntimeError("openrouter down")
        return {"title": title or text, "due_date": due_date, "due_time": None}

    async def no_classify(*a, **kw):
        raise AssertionError("комментарий к форварду не классифицируется")

    monkeypatch.setattr(llm, "parse_task", fake_parse)
    monkeypatch.setattr(llm, "classify_message", no_classify)
    return seen


def _fwd(text):
    return FakeMessage(text, forward_origin=SimpleNamespace(type="user", chat=None))


async def _later(delay, coro):
    await asyncio.sleep(delay)
    await coro


def test_написанное_руками_без_даты_на_сегодня(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    monkeypatch.setattr(storage, "get_projects_enabled", _async(False))
    monkeypatch.setattr(storage, "get_confirm_mode", _async("off"))
    _fake_llm(monkeypatch, {"title": "купить молоко", "due_date": None, "due_time": None})

    run(messages._handle_new_task(FakeMessage("купить молоко")))

    assert created[0]["due_date"] == config.local_today().isoformat()


def test_форвард_без_комментария_во_входящие_без_кнопок(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    _no_llm(monkeypatch)
    msg = _fwd("Статья про индексы в Postgres\nи ещё много текста")

    run(messages._handle_new_task(msg))

    assert created == [{
        "title": "Статья про индексы в Postgres", "due_date": None,
        "description": "Статья про индексы в Postgres\nи ещё много текста",
    }]
    assert "Добавил во входящие" in msg.answers[-1]
    assert "Всего во входящих: 3" in msg.answers[-1]
    assert msg.markups[-1] is None


def test_форвард_без_текста_получает_заглушку(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    _no_llm(monkeypatch)
    run(messages._handle_new_task(_fwd(None)))
    assert created[0]["title"] == "Пересланное сообщение"


def test_комментарий_без_даты_и_форвард_во_входящие(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    seen = _comment_parse(monkeypatch, due_date=None)
    comment = FakeMessage("прочитать про индексы")
    forward = _fwd("Статья про индексы")

    async def scenario():
        await asyncio.gather(
            messages._handle_new_task(comment),
            _later(0.01, messages._handle_new_task(forward)),
        )

    run(scenario())

    assert seen == [False], "сегодня по умолчанию для комментария не подставляем"
    assert len(created) == 1
    assert created[0]["title"] == "прочитать про индексы"
    assert created[0]["due_date"] is None
    assert "Статья про индексы" in created[0]["description"]


def test_комментарий_с_датой_задача_на_эту_дату(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    today = config.local_today().isoformat()
    _comment_parse(monkeypatch, due_date=today, title="Посмотреть это сообщение")
    monkeypatch.setattr(storage, "get_confirm_mode", _async("off"))
    comment = FakeMessage("Посмотреть это сообщение сегодня")
    forward = _fwd("у меня лежит, трафик ушёл на другие сервера")

    async def scenario():
        await asyncio.gather(
            messages._handle_new_task(comment),
            _later(0.01, messages._handle_new_task(forward)),
        )

    run(scenario())

    assert len(created) == 1
    assert created[0]["due_date"] == today
    assert created[0]["title"] == "Посмотреть это сообщение"
    assert "трафик ушёл" in created[0]["description"]
    replies = comment.answers + forward.answers
    assert not any("входящие" in r for r in replies)
    assert any("Задача создана" in r for r in replies)


def test_сбой_llm_на_комментарии_не_теряет_форвард(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    _comment_parse(monkeypatch, fail=True)

    async def scenario():
        await asyncio.gather(
            messages._handle_new_task(FakeMessage("глянуть сегодня")),
            _later(0.01, messages._handle_new_task(_fwd("Пост"))),
        )

    run(scenario())

    assert len(created) == 1
    assert created[0]["due_date"] is None
    assert created[0]["title"] == "глянуть сегодня"


def test_форвард_раньше_комментария(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    _comment_parse(monkeypatch)

    async def scenario():
        await asyncio.gather(
            messages._handle_new_task(_fwd("Пост")),
            _later(0.01, messages._handle_new_task(FakeMessage("глянуть"))),
        )

    run(scenario())
    assert [c["title"] for c in created] == ["глянуть"]


def test_несколько_форвардов_без_комментария_отдельными_задачами(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    _no_llm(monkeypatch)
    msgs = [_fwd(f"Пост {i}") for i in range(3)]

    async def scenario():
        await asyncio.gather(*(_later(0.01 * i, messages._handle_new_task(m)) for i, m in enumerate(msgs)))

    run(scenario())

    assert sorted(c["title"] for c in created) == ["Пост 0", "Пост 1", "Пост 2"]
    replies = [a for m in msgs for a in m.answers]
    assert len(replies) == 1, "один ответ на пачку"
    assert "Пост 0" in replies[0] and "Пост 2" in replies[0]


def test_несколько_форвардов_с_комментарием_одна_задача(monkeypatch, fast):
    created = _capture_create(monkeypatch)
    _comment_parse(monkeypatch)

    async def scenario():
        await asyncio.gather(
            messages._handle_new_task(FakeMessage("про индексы")),
            _later(0.01, messages._handle_new_task(_fwd("Пост A"))),
            _later(0.02, messages._handle_new_task(_fwd("Пост B"))),
        )

    run(scenario())

    assert len(created) == 1
    assert created[0]["title"] == "про индексы"
    assert "Пост A" in created[0]["description"] and "Пост B" in created[0]["description"]


def _async(value):
    async def f(*a, **kw):
        return value
    return f


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
