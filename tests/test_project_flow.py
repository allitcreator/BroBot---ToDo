"""Сессия интервью: потолок вопросов, сбой модели, накопление истории.

LLM и Telegram заменены заглушками — проверяется только логика сессии,
которая живёт в user_states и переживает рестарт бота.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import storage  # noqa: E402
from handlers import project_flow  # noqa: E402
from services import interview  # noqa: E402

USER = 777
CHAT = 42


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_chat_action(self, chat_id, action):
        pass

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append(text)


class FakeMessage:
    def __init__(self, bot):
        self.bot = bot
        self.chat = type("chat", (), {"id": CHAT})()
        self.from_user = type("user", (), {"id": USER})()
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


def _turn(question="Вопрос?", done=False, failed=False, coverage=None):
    return {
        "question": question,
        "coverage": coverage or interview.empty_coverage(),
        "done": done,
        "failed": failed,
    }


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def setup_module():
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "test.db")
    asyncio.set_event_loop(asyncio.new_event_loop())
    run(storage.init_db())


def teardown_module():
    run(storage.close_db())


def _reset():
    run(storage.clear_state(USER))


def test_старт_кладёт_первый_вопрос_в_историю(monkeypatch):
    _reset()
    monkeypatch.setattr(interview, "next_turn", lambda *a, **k: _fake(_turn("Зачем это?")))
    bot = FakeBot()

    run(project_flow.start_interview(bot, CHAT, USER, "хочу сделать бота"))

    state, data = run(storage.get_state(USER))
    assert state == project_flow.STATE_SESSION
    assert data["turn_count"] == 1
    assert data["history"] == [{"role": "assistant", "content": "Зачем это?"}]
    assert "Зачем это?" in bot.messages[0]


def test_сбой_модели_на_старте_не_заводит_сессию(monkeypatch):
    _reset()
    monkeypatch.setattr(interview, "next_turn", lambda *a, **k: _fake(_turn("нет связи", failed=True)))
    bot = FakeBot()

    run(project_flow.start_interview(bot, CHAT, USER, "идея"))

    state, _ = run(storage.get_state(USER))
    assert state is None
    assert bot.messages[0].startswith("❌")


def test_ответ_копится_в_истории_и_двигает_счётчик(monkeypatch):
    _reset()
    monkeypatch.setattr(interview, "next_turn", lambda *a, **k: _fake(_turn("Второй вопрос?")))
    message = FakeMessage(FakeBot())
    state_data = {"seed_text": "идея", "history": [{"role": "assistant", "content": "Первый?"}], "turn_count": 1}

    run(project_flow.handle_answer(message, state_data, "мой ответ"))

    _, data = run(storage.get_state(USER))
    assert data["turn_count"] == 2
    assert [h["content"] for h in data["history"]] == ["Первый?", "мой ответ", "Второй вопрос?"]


def test_сбой_модели_не_съедает_лимит_но_хранит_ответ(monkeypatch):
    _reset()
    monkeypatch.setattr(interview, "next_turn", lambda *a, **k: _fake(_turn("нет связи", failed=True)))
    message = FakeMessage(FakeBot())
    state_data = {"seed_text": "идея", "history": [{"role": "assistant", "content": "Первый?"}], "turn_count": 3}

    run(project_flow.handle_answer(message, state_data, "мой ответ"))

    _, data = run(storage.get_state(USER))
    assert data["turn_count"] == 3, "неудачный ход не должен тратить лимит вопросов"
    assert data["history"][-1] == {"role": "user", "content": "мой ответ"}


def test_потолок_вопросов_закрывает_интервью(monkeypatch):
    _reset()
    monkeypatch.setattr(config, "INTERVIEW_MAX_QUESTIONS", 3)
    monkeypatch.setattr(interview, "next_turn", lambda *a, **k: _fake(_turn("ещё вопрос?")))
    monkeypatch.setattr(interview, "compile_brief", lambda *a, **k: _fake({"title": "П", "steps": []}))
    message = FakeMessage(FakeBot())
    state_data = {"seed_text": "идея", "history": [], "turn_count": 3}

    run(project_flow.handle_answer(message, state_data, "последний ответ"))

    state, data = run(storage.get_state(USER))
    assert state == project_flow.STATE_REVIEW
    assert data["brief"]["title"] == "П"
    assert "потолок" in message.answers[0].lower()


def test_done_от_модели_ведёт_сразу_к_брифу(monkeypatch):
    _reset()
    monkeypatch.setattr(interview, "next_turn", lambda *a, **k: _fake(_turn("", done=True)))
    monkeypatch.setattr(interview, "compile_brief", lambda *a, **k: _fake({"title": "Готово", "steps": ["Шаг"]}))
    message = FakeMessage(FakeBot())
    state_data = {"seed_text": "идея", "history": [], "turn_count": 2}

    run(project_flow.handle_answer(message, state_data, "ответ"))

    state, data = run(storage.get_state(USER))
    assert state == project_flow.STATE_REVIEW
    assert data["brief"]["steps"] == ["Шаг"]


def test_длинный_бриф_режется_под_лимит_телеграма():
    assert len(project_flow.cut_text("я" * 9000)) <= project_flow.TELEGRAM_LIMIT + 1


async def _fake_value(value):
    return value


def _fake(value):
    return _fake_value(value)
