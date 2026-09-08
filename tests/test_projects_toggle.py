"""Выключатель фичи проектов в /settings.

Проекты — самая дорогая ветка бота: каждое входящее сообщение сначала уходит
в классификатор, а признанный проектом текст запускает интервью. Когда фича
выключена, ни того ни другого быть не должно — иначе выключатель косметический
и продолжает жечь вызовы LLM.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import storage  # noqa: E402
from handlers import callbacks, commands, keyboards, messages  # noqa: E402
from services import llm  # noqa: E402

USER = 778


class FakeMessage:
    def __init__(self):
        self.from_user = type("user", (), {"id": USER})()
        self.chat = type("chat", (), {"id": 42})()
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)

    async def edit_reply_markup(self, reply_markup=None):
        pass


class FakeCallback:
    def __init__(self):
        self.from_user = type("user", (), {"id": USER})()
        self.message = FakeMessage()
        self.bot = None
        self.answers = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def setup_module():
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "test.db")
    asyncio.set_event_loop(asyncio.new_event_loop())
    run(storage.init_db())


def teardown_module():
    run(storage.close_db())


def test_по_умолчанию_проекты_включены():
    assert run(storage.get_projects_enabled(USER)) is True


def test_выключатель_переживает_чтение():
    run(storage.set_projects_enabled(USER, False))
    assert run(storage.get_projects_enabled(USER)) is False

    run(storage.set_projects_enabled(USER, True))
    assert run(storage.get_projects_enabled(USER)) is True


def test_выключатель_не_сбивает_соседнюю_настройку():
    run(storage.set_confirm_mode(USER, "uncertain"))
    run(storage.set_projects_enabled(USER, False))

    assert run(storage.get_confirm_mode(USER)) == "uncertain"
    assert run(storage.get_research_enabled(USER)) is True


def test_при_выключенных_проектах_классификатор_не_зовётся(monkeypatch):
    run(storage.set_projects_enabled(USER, False))
    called = []

    async def _boom(text):
        called.append(text)
        return {"kind": "project", "reason": "не должно вызываться"}

    monkeypatch.setattr(llm, "classify_message", _boom)

    assert run(messages.classify_kind(USER, "хочу сделать бота")) == "task"
    assert called == [], "выключенная фича не должна тратить вызов LLM"


def test_при_включённых_проектах_решает_классификатор(monkeypatch):
    run(storage.set_projects_enabled(USER, True))

    async def _project(text):
        return {"kind": "project", "reason": "замысел"}

    monkeypatch.setattr(llm, "classify_message", _project)

    assert run(messages.classify_kind(USER, "хочу сделать бота")) == "project"


def test_команда_projects_при_выключенной_фиче_не_ходит_в_ms_todo(monkeypatch):
    run(storage.set_projects_enabled(USER, False))
    from services import projects

    async def _boom():
        raise AssertionError("сеть не должна дёргаться при выключенной фиче")

    monkeypatch.setattr(projects, "list_projects", _boom)
    message = FakeMessage()

    run(commands.cmd_projects(message))

    assert message.answers, "молчать в ответ на команду нельзя"
    assert "/settings" in message.answers[0]


def test_карточка_задачи_без_кнопки_проекта_когда_выключено():
    with_button = keyboards.confirm_task_kb(projects_enabled=True)
    without = keyboards.confirm_task_kb(projects_enabled=False)

    def _callbacks(kb):
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    assert "confirm:project" in _callbacks(with_button)
    assert "confirm:project" not in _callbacks(without)
    assert "confirm:create" in _callbacks(without), "остальные кнопки должны остаться"


def test_старая_кнопка_проекта_не_запускает_интервью(monkeypatch):
    """Кнопки в истории Telegram живут и после выключения фичи."""
    run(storage.set_projects_enabled(USER, False))
    run(storage.save_pending_task(USER, {"title": "идея", "source_text": "хочу бота"}))

    async def _boom(*a, **k):
        raise AssertionError("интервью не должно запускаться при выключенной фиче")

    monkeypatch.setattr(callbacks.project_flow, "start_interview", _boom)
    callback = FakeCallback()

    run(callbacks.cb_confirm_project(callback))

    assert callback.answers, "тапнувшую кнопку нельзя оставлять без ответа"
    assert "/settings" in callback.answers[0] + "".join(callback.message.answers)
    assert run(storage.get_pending_task(USER)), "задачу в подтверждении не теряем"


def test_клавиатура_настроек_показывает_состояние_проектов():
    on = keyboards.settings_kb("all", research_enabled=True, projects_enabled=True)
    off = keyboards.settings_kb("all", research_enabled=True, projects_enabled=False)

    def _callbacks(kb):
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    assert "settings:projects:off" in _callbacks(on), "включённую фичу предлагаем выключить"
    assert "settings:projects:on" in _callbacks(off)
