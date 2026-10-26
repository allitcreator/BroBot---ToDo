"""Ритуалы разбора: когда слать сводку, что в ней и как не прислать дважды.

Сводка — утром и вечером, только если есть что решать. Рестарт бота,
сбой Telegram или MS To Do не должны давать ни дубля, ни потерянной сводки.
"""
import asyncio
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import storage  # noqa: E402
from services import google_calendar, ms_todo, rituals  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
OWNER = 4242
TUE = date(2026, 10, 27)  # вторник
SAT = date(2026, 10, 31)

DEFAULTS = {
    "morning_enabled": True, "morning_time": "09:30",
    "evening_enabled": True, "evening_time": "20:30",
    "stuck_days": 14, "weekend_rituals": False,
}


def at(day: date, hm: str) -> datetime:
    return datetime.combine(day, datetime.strptime(hm, "%H:%M").time(), tzinfo=MSK)


def run_(status, **kw):
    base = {"status": status, "updated_at": None, "snooze_at": None, "snoozed": 0, "message_id": None, "done_at": None}
    return {**base, **kw}


# --- когда слать: чистая функция ---

@pytest.mark.parametrize("hm, expected", [
    ("09:29", []),
    ("09:30", ["morning"]),
    ("11:29", ["morning"]),
    ("11:30", []),            # окно опоздания — 2 часа
    ("20:30", ["evening"]),
])
def test_время_и_окно(hm, expected):
    assert rituals.due_rituals(at(TUE, hm), DEFAULTS, {}) == expected


def test_выключенная_сводка_не_шлётся():
    s = {**DEFAULTS, "morning_enabled": False}
    assert rituals.due_rituals(at(TUE, "09:30"), s, {}) == []


def test_выходные_по_умолчанию_молчат():
    assert rituals.due_rituals(at(SAT, "09:30"), DEFAULTS, {}) == []
    assert rituals.due_rituals(at(SAT, "09:30"), {**DEFAULTS, "weekend_rituals": True}, {}) == ["morning"]


@pytest.mark.parametrize("status", ["sending", "sent", "skipped", "dismissed"])
def test_уже_отправленная_или_в_работе_не_повторяется(status):
    now = at(TUE, "09:40")
    runs = {"morning": run_(status, updated_at=now - timedelta(minutes=1))}
    assert rituals.due_rituals(now, DEFAULTS, runs) == []


def test_брошенная_отправка_после_падения_повторяется():
    now = at(TUE, "09:50")
    runs = {"morning": run_("sending", updated_at=now - timedelta(minutes=11))}
    assert rituals.due_rituals(now, DEFAULTS, runs) == ["morning"]


def test_сбой_повторяется_не_чаще_раза_в_5_минут():
    now = at(TUE, "09:40")
    assert rituals.due_rituals(now, DEFAULTS, {"morning": run_("failed", updated_at=now - timedelta(minutes=4))}) == []
    assert rituals.due_rituals(now, DEFAULTS, {"morning": run_("failed", updated_at=now - timedelta(minutes=5))}) == ["morning"]


def test_позже_повторяет_через_час_один_раз():
    now = at(TUE, "10:31")
    snoozed = run_("snoozed", snooze_at=at(TUE, "10:30"), snoozed=1)
    assert rituals.due_rituals(now, DEFAULTS, {"morning": snoozed}) == ["morning"]
    assert rituals.due_rituals(at(TUE, "10:29"), DEFAULTS, {"morning": snoozed}) == []
    # Повтор после «Позже» не ограничен окном в 2 часа от 9:30.
    assert rituals.due_rituals(at(TUE, "12:00"), DEFAULTS, {"morning": snoozed}) == ["morning"]


def test_пройденный_ритуал_не_напоминает_после_позже():
    snoozed = run_("snoozed", snooze_at=at(TUE, "10:30"), snoozed=1, done_at=at(TUE, "10:00"))
    assert rituals.due_rituals(at(TUE, "10:31"), DEFAULTS, {"morning": snoozed}) == []


# --- срезы утреннего разбора ---

def _due(day):
    return {"dateTime": f"{day.isoformat()}T00:00:00.0000000", "timeZone": "UTC"}


def _created(days_ago, today=TUE):
    return (datetime.combine(today, datetime.min.time()) - timedelta(days=days_ago)).strftime("%Y-%m-%dT10:00:00Z")


def test_застряло_старые_без_даты_и_просроченные_до_трёх():
    tasks = [
        {"id": "old-inbox", "createdDateTime": _created(40)},
        {"id": "old-overdue", "createdDateTime": _created(30), "dueDateTime": _due(TUE - timedelta(days=3))},
        {"id": "old-today", "createdDateTime": _created(50), "dueDateTime": _due(TUE)},
        {"id": "old-future", "createdDateTime": _created(60), "dueDateTime": _due(TUE + timedelta(days=5))},
        {"id": "old-inbox-2", "createdDateTime": _created(20)},
        {"id": "old-inbox-3", "createdDateTime": _created(15)},
        {"id": "fresh-inbox", "createdDateTime": _created(2)},
        {"id": "fresh-overdue", "createdDateTime": _created(3), "dueDateTime": _due(TUE - timedelta(days=1))},
    ]
    s = rituals.morning_slices(tasks, TUE, stuck_days=14)
    ids = lambda xs: [t["id"] for t in xs]
    # Самые старые три; на сегодня и на будущее — не застряли.
    assert ids(s["stuck"]) == ["old-inbox", "old-overdue", "old-inbox-2"]
    # Показанные в «Застряло» в других шагах не повторяются.
    assert ids(s["overdue"]) == ["fresh-overdue"]
    assert ids(s["inbox"]) == ["old-inbox-3", "fresh-inbox"]
    assert ids(s["today"]) == ["old-today"]


# --- тексты ---

def test_текст_утренней_сводки():
    text = rituals.morning_text(today=4, meetings=2, overdue=1, stuck=2, inbox=3, stuck_days=14)
    assert text == (
        "☀️ Доброе утро! Сегодня 4 задачи и 2 встречи.\n"
        "Ждут решения:\n"
        "• 1 просрочена\n"
        "• 2 застряли (висят больше 2 недель)\n"
        "• 3 во входящих\n"
        "Займёт пару минут."
    )


def test_текст_утренней_сводки_без_календаря_и_пустых_строк():
    text = rituals.morning_text(today=1, meetings=None, overdue=0, stuck=0, inbox=5, stuck_days=7)
    assert text.startswith("☀️ Доброе утро! Сегодня 1 задача.\n")
    assert "просроч" not in text and "застрял" not in text
    assert "• 5 во входящих" in text


def test_текст_вечерней_сводки():
    assert rituals.evening_text(done=3, left=3) == (
        "🌙 Закрываем день? Сделано 3 из 6.\nОсталось 3 — отметим и перенесём, 30 секунд."
    )


# --- tick целиком ---

class FakeBot:
    def __init__(self):
        self.sent, self.deleted, self.fail = [], [], 0

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("Telegram timeout")
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "USER_TIMEZONE", "Europe/Moscow")
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER)
    monkeypatch.setattr(config, "MINIAPP_URL", "https://todo.example/")
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "rit.db")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(storage.init_db())

    state = {
        "tasks": [{"id": "i1", "createdDateTime": _created(1)}],
        "completed": [],
        "events": [],
        "graph_fail": 0,
    }

    async def all_tasks(list_id=None):
        if state["graph_fail"]:
            state["graph_fail"] -= 1
            raise RuntimeError("MS Graph 503")
        return state["tasks"]

    async def completed_since(day):
        return state["completed"]

    async def events_day(day):
        return state["events"]

    monkeypatch.setattr(ms_todo, "get_all_tasks", all_tasks)
    monkeypatch.setattr(ms_todo, "get_completed_since", completed_since)
    monkeypatch.setattr(google_calendar, "get_events_day", events_day)
    yield SimpleNamespace(run=loop.run_until_complete, state=state, bot=FakeBot())
    loop.run_until_complete(storage.close_db())
    loop.close()


def test_сводка_уходит_один_раз_и_рестарт_не_дублирует(env):
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    assert len(env.bot.sent) == 1
    chat_id, text, markup = env.bot.sent[0]
    assert chat_id == OWNER and "1 во входящих" in text
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == "https://todo.example/?ritual=morning"

    env.run(rituals.tick(env.bot, at(TUE, "09:31")))
    env.run(storage.close_db())   # «рестарт»
    env.run(storage.init_db())
    env.run(rituals.tick(env.bot, at(TUE, "09:32")))
    assert len(env.bot.sent) == 1


def test_нечего_решать_утром_бот_молчит_и_не_пересчитывает(env):
    env.state["tasks"] = []
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    env.state["tasks"] = [{"id": "i1", "createdDateTime": _created(1)}]
    env.run(rituals.tick(env.bot, at(TUE, "09:31")))
    assert env.bot.sent == []


def test_сбой_telegram_повторяется_через_5_минут(env):
    env.bot.fail = 1
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    env.run(rituals.tick(env.bot, at(TUE, "09:32")))
    assert env.bot.sent == []
    env.run(rituals.tick(env.bot, at(TUE, "09:35")))
    assert len(env.bot.sent) == 1


def test_сбой_ms_todo_тоже_повторяется(env):
    env.state["graph_fail"] = 1
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    env.run(rituals.tick(env.bot, at(TUE, "09:35")))
    assert len(env.bot.sent) == 1


def test_вечер_только_если_остались_задачи_на_сегодня(env):
    env.run(rituals.tick(env.bot, at(TUE, "20:30")))
    assert env.bot.sent == []
    env.run(storage.delete_ritual_run("evening", TUE.isoformat()))
    env.state["tasks"] = [{"id": "t1", "createdDateTime": _created(1), "dueDateTime": _due(TUE)}]
    env.state["completed"] = [{"id": "c1"}, {"id": "c2"}]
    env.run(rituals.tick(env.bot, at(TUE, "20:30")))
    assert "Сделано 2 из 3" in env.bot.sent[0][1]


def test_позже_удаляет_сводку_и_присылает_через_час_без_кнопки_позже(env):
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    first_id = 101
    env.run(rituals.snooze(env.bot, "morning", TUE.isoformat(), at(TUE, "09:40")))
    assert env.bot.deleted == [first_id]

    env.run(rituals.tick(env.bot, at(TUE, "10:39")))
    assert len(env.bot.sent) == 1
    env.run(rituals.tick(env.bot, at(TUE, "10:40")))
    assert len(env.bot.sent) == 2
    labels = [b.text for b in env.bot.sent[1][2].inline_keyboard[0]]
    assert labels == ["Разобрать ↗"]
    env.run(rituals.tick(env.bot, at(TUE, "11:41")))
    assert len(env.bot.sent) == 2


def test_не_сегодня_выключает_вечер_до_завтра(env):
    env.state["tasks"] = [{"id": "t1", "createdDateTime": _created(1), "dueDateTime": _due(TUE)}]
    env.run(rituals.tick(env.bot, at(TUE, "20:30")))
    env.run(rituals.dismiss(env.bot, "evening", TUE.isoformat()))
    env.run(rituals.tick(env.bot, at(TUE, "21:30")))
    assert len(env.bot.sent) == 1 and env.bot.deleted == [101]


def test_ritual_done_отмечается(env):
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    env.run(rituals.mark_done("morning", TUE.isoformat(), at(TUE, "09:35")))
    run = env.run(storage.get_ritual_run("morning", TUE.isoformat()))
    assert run["done_at"] is not None


# --- кнопки в чате и команда /ritual ---

class FakeCallback:
    def __init__(self, data, bot):
        self.data = data
        self.from_user = SimpleNamespace(id=OWNER)
        self.bot = bot
        self.answers = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)


def test_кнопка_позже_в_чате(env, monkeypatch):
    from handlers import callbacks
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    called = []

    async def fake_snooze(bot, kind, day, now):
        called.append((kind, day))
        return True

    monkeypatch.setattr(rituals, "snooze", fake_snooze)
    cb = FakeCallback(f"ritual:snooze:morning:{TUE.isoformat()}", env.bot)
    env.run(callbacks.cb_ritual(cb))
    assert called == [("morning", TUE.isoformat())]
    assert "час" in cb.answers[0]


def test_кнопка_не_сегодня_в_чате(env, monkeypatch):
    from handlers import callbacks
    called = []

    async def fake_dismiss(bot, kind, day):
        called.append((kind, day))

    monkeypatch.setattr(rituals, "dismiss", fake_dismiss)
    cb = FakeCallback(f"ritual:dismiss:evening:{TUE.isoformat()}", env.bot)
    env.run(callbacks.cb_ritual(cb))
    assert called == [("evening", TUE.isoformat())]


def test_команда_ritual_шлёт_сводку_сразу(env):
    from handlers import commands

    class Msg:
        def __init__(self):
            self.from_user = SimpleNamespace(id=OWNER)
            self.bot = env.bot
            self.answers = []

        async def answer(self, text, **kw):
            self.answers.append(text)

    msg = Msg()
    env.run(commands.cmd_ritual(msg, SimpleNamespace(args="morning")))
    assert len(env.bot.sent) == 1 and "во входящих" in env.bot.sent[0][1]
    labels = [b.text for b in env.bot.sent[0][2].inline_keyboard[0]]
    assert labels == ["Разобрать ↗"], "сводка по команде — без «Позже»"

    env.state["tasks"] = []
    msg = Msg()
    env.run(commands.cmd_ritual(msg, SimpleNamespace(args="evening")))
    assert "нечего" in msg.answers[0]


@pytest.mark.parametrize("status", ["failed", "sending"])
def test_пройденный_ритуал_не_повторяет_сбойную_сводку(status):
    now = at(TUE, "09:50")
    runs = {"morning": run_(status, updated_at=now - timedelta(minutes=20), done_at=now - timedelta(minutes=1))}
    assert rituals.due_rituals(now, DEFAULTS, runs) == []


def test_без_адреса_мини_аппа_сводки_не_шлются(env, monkeypatch):
    monkeypatch.setattr(config, "MINIAPP_URL", "")
    env.run(rituals.tick(env.bot, at(TUE, "09:30")))
    assert env.bot.sent == []
    assert env.run(storage.get_ritual_run("morning", TUE.isoformat())) is None


def test_позже_без_сводки_честно_отвечает(env):
    from handlers import callbacks
    cb = FakeCallback(f"ritual:snooze:morning:{TUE.isoformat()}", env.bot)
    env.run(callbacks.cb_ritual(cb))
    assert "час" not in cb.answers[0]
