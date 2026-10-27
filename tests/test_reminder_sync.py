"""Напоминания из MS Todo в Telegram: синк, отправка с потолком ретраев, кнопки.

Mac ставит reminderDateTime в задаче MS Todo, бот сам заводит запись в
`reminders` и доставляет её один раз. Без сети: Graph и Telegram — моки.
"""
import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
)
from aiogram.methods import SendMessage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot as bot_module  # noqa: E402
import config  # noqa: E402
from db import storage  # noqa: E402
from handlers import callbacks  # noqa: E402
from services import ms_todo, reminder_sync  # noqa: E402

OWNER = 4242
FMT = "%Y-%m-%dT%H:%M:%S"


def utc(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime(FMT)


def task(tid, title, delta, on=True, status="notStarted"):
    return {
        "id": tid, "title": title, "status": status,
        "isReminderOn": on,
        "reminderDateTime": {"dateTime": utc(delta) + ".0000000", "timeZone": "UTC"} if delta is not None else None,
    }


METHOD = SendMessage(chat_id=1, text="x")


class FakeBot:
    def __init__(self):
        self.sent, self.fail, self.calls = [], 0, 0
        self.exc = lambda: RuntimeError("Telegram timeout")

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise self.exc()
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=1)


class FakeMessage:
    text = "⏰ Напоминание: A"

    def __init__(self):
        self.markups = []

    async def edit_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=OWNER)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER)
    monkeypatch.setattr(config, "USER_TIMEZONE", "Europe/Moscow")
    config.DB_PATH = str(Path(tempfile.mkdtemp()) / "rem.db")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(storage.init_db())
    bot_module._retry_after.clear()
    bot_module._sent_unmarked.clear()
    state = {"tasks": [], "graph_fail": False, "set": [], "done": [], "done_fail": False, "removed": []}

    async def all_tasks(list_id=None):
        if state["graph_fail"]:
            raise RuntimeError("MS Graph 503")
        return state["tasks"]

    async def set_reminder(task_id, fire_at):
        state["set"].append((task_id, fire_at))

    async def remove_reminder(task_id):
        state["removed"].append(task_id)

    async def complete_task(task_id, list_id=None):
        if state["done_fail"]:
            raise RuntimeError("MS Graph 503")
        state["done"].append(task_id)

    monkeypatch.setattr(ms_todo, "get_all_tasks", all_tasks)
    monkeypatch.setattr(ms_todo, "set_reminder", set_reminder)
    monkeypatch.setattr(ms_todo, "complete_task", complete_task)
    monkeypatch.setattr(ms_todo, "remove_reminder", remove_reminder)
    yield SimpleNamespace(run=loop.run_until_complete, state=state, bot=FakeBot(), mp=monkeypatch)
    loop.run_until_complete(storage.close_db())
    loop.close()


def prime(env, ago: timedelta):
    """Последний успешный синк был ago назад."""
    env.run(storage.set_sync_state(reminder_sync.LAST_OK_KEY, utc(-ago)))


async def _rows():
    async with storage._db.execute("SELECT task_id, fire_at, text, chat_id FROM reminders ORDER BY id") as c:
        return await c.fetchall()


# --- синк ---

def test_синк_заводит_запись_один_раз(env):
    env.state["tasks"] = [task("t1", "Позвонить", timedelta(hours=2))]
    env.run(reminder_sync.sync())
    env.run(reminder_sync.sync())
    rows = env.run(_rows())
    assert len(rows) == 1
    assert rows[0][0] == "t1" and rows[0][2] == "⏰ Напоминание: Позвонить" and rows[0][3] == OWNER


def test_прошлое_вне_окна_не_заводится_а_свежее_заводится(env):
    prime(env, timedelta(minutes=5))
    env.state["tasks"] = [
        task("old", "Давно", -timedelta(hours=3)),
        task("fresh", "Только что", -timedelta(minutes=4)),
        task("off", "Выключено", timedelta(hours=1), on=False),
        task("none", "Без даты", None),
        task("done", "Закрыта", timedelta(hours=1), status="completed"),
    ]
    env.run(reminder_sync.sync())
    assert [r[0] for r in env.run(_rows())] == ["fresh"]


def test_снятое_в_mstodo_снимается_в_reminders(env):
    env.state["tasks"] = [task("t1", "A", timedelta(hours=2)), task("t2", "B", timedelta(hours=3))]
    env.run(reminder_sync.sync())
    env.state["tasks"] = [task("t1", "A", None, on=False)]   # t1 снято, t2 закрыта
    env.run(reminder_sync.sync())
    assert env.run(_rows()) == []


def test_перенос_в_mstodo_заменяет_запись(env):
    env.state["tasks"] = [task("t1", "A", timedelta(hours=2))]
    env.run(reminder_sync.sync())
    env.state["tasks"] = [task("t1", "A", timedelta(hours=5))]
    env.run(reminder_sync.sync())
    rows = env.run(_rows())
    assert len(rows) == 1 and rows[0][1] > utc(timedelta(hours=4))


def test_ручные_напоминания_синк_не_трогает(env):
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=1)), "⏰ ручное"))                 # без task_id
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=1)), "⏰ tg", task_id="tg1", source="tg"))
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=1)), "⏰ старая", task_id="old1"))  # source NULL
    env.state["tasks"] = []
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 3


def test_запись_из_чата_с_mstodo_не_дублируется(env):
    fire = timedelta(hours=2)
    t = task("t1", "A", fire)
    env.run(storage.save_reminder(OWNER, t["reminderDateTime"]["dateTime"].split(".")[0], "⏰ Напоминание: A",
                                  task_id="t1", source="mstodo"))
    env.state["tasks"] = [t]
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1


def test_падение_graph_ничего_не_ломает(env):
    env.state["tasks"] = [task("t1", "A", timedelta(hours=2))]
    env.run(reminder_sync.sync())
    env.state["graph_fail"] = True
    with pytest.raises(RuntimeError):
        env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1          # записи на месте
    env.run(bot_module._sync_tick())            # обёртка цикла исключение глотает


def test_доставленное_не_возвращается_синком(env):
    prime(env, timedelta(minutes=5))
    env.state["tasks"] = [task("t1", "A", -timedelta(minutes=2))]
    env.run(reminder_sync.sync())
    env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1
    env.run(reminder_sync.sync())
    env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1 and env.run(_rows()) == []


# --- отправка ---

def test_кнопки_только_у_напоминаний_с_задачей(env):
    env.run(storage.save_reminder(OWNER, utc(-timedelta(seconds=5)), "⏰ ручное"))
    env.run(storage.save_reminder(OWNER, utc(-timedelta(seconds=5)), "⏰ задача", task_id="t1"))
    env.run(bot_module.deliver_due(env.bot))
    by_text = {t: m for _, t, m in env.bot.sent}
    assert by_text["⏰ ручное"] is None
    datas = [b.callback_data for row in by_text["⏰ задача"].inline_keyboard for b in row]
    assert len(datas) == 3 and all(len(d.encode()) <= 64 for d in datas)
    assert [d.split(":")[1] for d in datas] == ["done", "1h", "tom"]


def test_потолок_ретраев(env):
    env.run(storage.save_reminder(OWNER, utc(-timedelta(seconds=5)), "⏰ x", task_id="t1"))
    env.bot.fail = 100
    for _ in range(bot_module.REMINDER_MAX_ATTEMPTS - 1):
        env.run(bot_module.deliver_due(env.bot))
    assert len(env.run(_rows())) == 1
    env.run(bot_module.deliver_due(env.bot))
    assert env.run(_rows()) == []
    env.bot.fail = 0
    env.run(bot_module.deliver_due(env.bot))
    assert env.bot.sent == []


def test_send_ok_delete_fail_не_шлёт_повторно(env):
    env.run(storage.save_reminder(OWNER, utc(-timedelta(seconds=5)), "⏰ x", task_id="t1"))
    real = storage.delete_reminder
    calls = {"n": 0}

    async def flaky(rid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        await real(rid)

    env.mp.setattr(storage, "delete_reminder", flaky)
    env.run(bot_module.deliver_due(env.bot))
    env.run(bot_module.deliver_due(env.bot))
    env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1
    assert env.run(_rows()) == []


# --- кнопки ---

def test_готово_закрывает_задачу_и_снимает_напоминания(env):
    key = env.run(storage.register_task_id("t1"))
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=1)), "⏰ x", task_id="t1"))
    cb = FakeCallback(f"rem:done:{key}")
    env.run(callbacks.cb_reminder_fire(cb))
    assert env.state["done"] == ["t1"] and env.run(_rows()) == []
    assert cb.message.markups == [None]


def test_готово_при_сбое_graph_не_убирает_кнопки(env):
    key = env.run(storage.register_task_id("t1"))
    env.state["done_fail"] = True
    cb = FakeCallback(f"rem:done:{key}")
    env.run(callbacks.cb_reminder_fire(cb))
    assert env.state["done"] == [] and cb.message.markups == []


def test_плюс_час_переставляет_и_в_mstodo_и_в_reminders(env):
    key = env.run(storage.register_task_id("t1"))
    cb = FakeCallback(f"rem:1h:{key}")
    before = datetime.now(timezone.utc)
    env.run(callbacks.cb_reminder_fire(cb))
    (tid, fire), = env.state["set"]
    assert tid == "t1"
    got = datetime.strptime(fire, FMT).replace(tzinfo=timezone.utc)
    assert timedelta(minutes=59) < got - before < timedelta(minutes=61)
    rows = env.run(_rows())
    assert [(r[0], r[1]) for r in rows] == [("t1", fire)]
    # синк не плодит второй экземпляр и не снимает его
    env.state["tasks"] = [{"id": "t1", "title": "A", "status": "notStarted", "isReminderOn": True,
                           "reminderDateTime": {"dateTime": fire + ".0000000", "timeZone": "UTC"}}]
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1
    # в MS Todo поставили другое время — строка кнопки заменяется, а не копится
    env.state["tasks"][0]["reminderDateTime"] = {"dateTime": utc(timedelta(hours=6)) + ".0000000", "timeZone": "UTC"}
    env.run(reminder_sync.sync())
    rows = env.run(_rows())
    assert len(rows) == 1 and rows[0][1] == utc(timedelta(hours=6))


def test_завтра_ставит_9_утра_по_местному(env):
    key = env.run(storage.register_task_id("t1"))
    cb = FakeCallback(f"rem:tom:{key}")
    env.run(callbacks.cb_reminder_fire(cb))
    (_, fire), = env.state["set"]
    from zoneinfo import ZoneInfo
    local = datetime.strptime(fire, FMT).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
    assert local.date() == config.local_today() + timedelta(days=1) and (local.hour, local.minute) == (9, 0)
    assert len(env.run(_rows())) == 1


# --- ревью: ошибки Telegram ---

def _due(env, task_id="t1", ago=timedelta(seconds=5)):
    env.run(storage.save_reminder(OWNER, utc(-ago), "⏰ x", task_id=task_id))


@pytest.mark.parametrize("exc", [
    lambda: TelegramForbiddenError(METHOD, "bot was blocked"),
    lambda: TelegramBadRequest(METHOD, "chat not found"),
])
def test_постоянная_ошибка_снимает_запись_сразу(env, exc):
    _due(env)
    env.bot.fail, env.bot.exc = 100, exc
    env.run(bot_module.deliver_due(env.bot))
    assert env.run(_rows()) == [] and env.bot.calls == 1


def test_временная_ошибка_не_считается_попыткой_первые_2_часа(env):
    _due(env)
    env.bot.fail, env.bot.exc = 100, lambda: TelegramNetworkError(METHOD, "timeout")
    for _ in range(bot_module.REMINDER_MAX_ATTEMPTS + 3):
        env.run(bot_module.deliver_due(env.bot))
    assert len(env.run(_rows())) == 1 and env.bot.calls == bot_module.REMINDER_MAX_ATTEMPTS + 3


def test_таймаут_тоже_временная_ошибка(env):
    _due(env)
    env.bot.fail, env.bot.exc = 100, lambda: asyncio.TimeoutError()
    for _ in range(bot_module.REMINDER_MAX_ATTEMPTS + 1):
        env.run(bot_module.deliver_due(env.bot))
    assert len(env.run(_rows())) == 1


def test_временная_ошибка_после_2_часов_идёт_в_потолок(env):
    _due(env, ago=timedelta(hours=3))
    env.bot.fail, env.bot.exc = 100, lambda: TelegramNetworkError(METHOD, "timeout")
    for _ in range(bot_module.REMINDER_MAX_ATTEMPTS):
        env.run(bot_module.deliver_due(env.bot))
    assert env.run(_rows()) == []


def test_retry_after_ждёт_указанное_время(env):
    _due(env)
    env.bot.fail, env.bot.exc = 1, lambda: TelegramRetryAfter(METHOD, "flood", 60)
    env.run(bot_module.deliver_due(env.bot))
    env.run(bot_module.deliver_due(env.bot))
    assert env.bot.calls == 1                       # пауза ещё не прошла
    bot_module._retry_after.clear()
    env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1 and env.run(_rows()) == []


# --- ревью: пометка и устойчивость прохода ---

def test_сбой_пометки_не_шлёт_каждые_30_секунд(env):
    _due(env)
    real = storage.mark_reminder_sent
    calls = {"n": 0}

    async def flaky(rid):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("database is locked")
        await real(rid)

    env.mp.setattr(storage, "mark_reminder_sent", flaky)
    for _ in range(4):
        env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1 and env.run(_rows()) == []


def test_сбой_учёта_попыток_не_обрывает_проход(env):
    _due(env, "a")
    _due(env, "b")
    env.bot.fail = 1

    async def broken(rid):
        raise RuntimeError("database is locked")

    env.mp.setattr(storage, "bump_reminder_attempts", broken)
    env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1                   # второе ушло, несмотря на сбой первого


def test_пара_из_журнала_не_отправляется_повторно(env):
    _due(env)
    env.run(storage.mark_reminder_sent(env.run(storage.get_due_reminders())[0]["id"]))
    env.run(storage.delete_sent_reminders())
    _due(env)                                         # та же пара не нужна: делаем свежую с тем же временем
    rid = env.run(storage.get_due_reminders())[0]
    # подменяем пару в журнале на пару этой строки
    async def log_pair():
        await storage._db.execute(
            "INSERT OR IGNORE INTO reminder_log (task_id, fire_at, logged_at) "
            "SELECT task_id, fire_at, datetime('now') FROM reminders WHERE id = ?", (rid["id"],))
        await storage._db.commit()
    env.run(log_pair())
    env.run(bot_module.deliver_due(env.bot))
    assert env.bot.sent == [] and env.run(_rows()) == []


# --- ревью: зеркало MS Todo ---

def test_кнопка_mstodo_потом_снятие_в_mstodo_снимает_строку(env):
    t = task("t1", "A", timedelta(hours=2))
    env.run(storage.save_reminder(OWNER, t["reminderDateTime"]["dateTime"].split(".")[0], "⏰ Напоминание: A",
                                  task_id="t1", source="mstodo"))
    env.state["tasks"] = [task("t1", "A", None, on=False)]
    env.run(reminder_sync.sync())
    assert env.run(_rows()) == []


def test_только_telegram_и_старые_строки_синк_не_снимает(env):
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=2)), "⏰ tg", task_id="t1", source="tg"))
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=2)), "⏰ старая", task_id="t2"))
    env.state["tasks"] = [task("t1", "A", None, on=False), task("t2", "B", None, on=False)]
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 2


# --- ревью: окно и первый проход ---

def test_первый_проход_после_выкладки_без_окна(env):
    # MS Todo помнит уже доставленное старым механизмом напоминание, журнал пуст
    env.state["tasks"] = [task("t1", "Доставлено", -timedelta(minutes=3))]
    env.run(reminder_sync.sync())
    assert env.run(_rows()) == []
    env.state["tasks"].append(task("t2", "Новое", timedelta(minutes=30)))
    env.run(reminder_sync.sync())
    assert [r[0] for r in env.run(_rows())] == ["t2"]


def test_окно_растёт_до_последнего_успешного_синка_но_не_больше_6_часов(env):
    prime(env, timedelta(hours=2))
    env.state["tasks"] = [task("a", "A", -timedelta(minutes=90)), task("b", "B", -timedelta(hours=3))]
    env.run(reminder_sync.sync())
    assert [r[0] for r in env.run(_rows())] == ["a"]

    prime(env, timedelta(hours=10))
    env.state["tasks"] = [task("c", "C", -timedelta(hours=5)), task("d", "D", -timedelta(hours=7))]
    env.run(reminder_sync.sync())
    assert sorted(r[0] for r in env.run(_rows())) == ["c"]


def test_упавший_синк_не_двигает_окно(env):
    prime(env, timedelta(hours=1))
    env.state["graph_fail"] = True
    with pytest.raises(RuntimeError):
        env.run(reminder_sync.sync())
    env.state["graph_fail"] = False
    env.state["tasks"] = [task("a", "A", -timedelta(minutes=50))]
    env.run(reminder_sync.sync())
    assert [r[0] for r in env.run(_rows())] == ["a"]


# --- ревью: ручные правки и кэш ---

def test_ручное_удаление_пишет_пару_в_журнал_и_кэш_не_воскрешает(env):
    t = task("t1", "A", timedelta(hours=2))
    env.state["tasks"] = [t]
    env.run(reminder_sync.sync())
    env.run(storage.delete_telegram_reminder_by_task("t1"))
    env.run(reminder_sync.sync())                    # get_all_tasks отдаёт устаревший кэш
    assert env.run(_rows()) == []


def test_перенос_строки_нормализует_формат_и_пишет_старую_пару(env):
    rid = env.run(storage.save_reminder(OWNER, utc(timedelta(hours=1)), "⏰ x", task_id="t1", source="sync"))
    old = env.run(_rows())[0][1]
    env.run(storage.set_reminder_fire_at(rid, "2030-01-01T10:00:00.1234+00:00"))
    assert env.run(_rows())[0][1] == "2030-01-01T10:00:00"
    assert env.run(storage.reminder_pair_known("t1", old))
    env.state["tasks"] = [{"id": "t1", "title": "x", "status": "notStarted", "isReminderOn": True,
                           "reminderDateTime": {"dateTime": "2030-01-01T10:00:00.0000000", "timeZone": "UTC"}}]
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1


# --- ревью: кривые данные Graph ---

@pytest.mark.parametrize("rdt", [
    {"dateTime": "мусор", "timeZone": "UTC"},
    {"dateTime": "2030-01-01T10:00:00.0000000", "timeZone": "Nowhere/Bad"},
])
def test_нераспознанное_время_задачу_пропускает_и_строки_не_снимает(env, rdt):
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=2)), "⏰ x", task_id="t1", source="sync"))
    env.state["tasks"] = [{"id": "t1", "title": "x", "status": "notStarted", "isReminderOn": True,
                           "reminderDateTime": rdt}]
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1


def test_усечённый_список_строки_не_снимает(env):
    env.run(storage.save_reminder(OWNER, utc(timedelta(hours=2)), "⏰ x", task_id="t1", source="sync"))
    env.state["tasks"] = []
    env.mp.setattr(ms_todo, "last_fetch_truncated", lambda: True)
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1


def test_get_tasks_помечает_усечение(env):
    ms_todo.invalidate_cache()
    pages = iter([{"value": [{"id": str(i)}], "@odata.nextLink": ms_todo.GRAPH_URL + "/next"} for i in range(10)])

    async def fake_request(method, path, params=None, **kw):
        return next(pages)

    env.mp.setattr(ms_todo, "_request", fake_request)
    env.run(ms_todo.get_tasks("status ne 'completed'"))
    assert ms_todo.last_fetch_truncated() is True
    ms_todo.invalidate_cache()


# --- ревью: миграция ---

def test_миграция_старой_схемы(monkeypatch):
    import sqlite3
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER)
    path = str(Path(tempfile.mkdtemp()) / "old.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
                "fire_at TEXT NOT NULL, text TEXT NOT NULL, task_id TEXT)")
    soon = utc(timedelta(hours=1))
    con.execute("INSERT INTO reminders (chat_id, fire_at, text, task_id) VALUES (?, ?, ?, ?)",
                (OWNER, utc(-timedelta(minutes=1)), "⏰ ручное", None))
    con.execute("INSERT INTO reminders (chat_id, fire_at, text, task_id) VALUES (?, ?, ?, ?)",
                (OWNER, soon, "⏰ из MS Todo", "t1"))
    con.commit()
    con.close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(storage.init_db())
        loop.run_until_complete(storage.close_db())
        loop.run_until_complete(storage.init_db())          # повторный старт — без падения

        async def all_tasks(list_id=None):
            return [{"id": "t1", "title": "A", "status": "notStarted", "isReminderOn": True,
                     "reminderDateTime": {"dateTime": soon + ".0000000", "timeZone": "UTC"}}]

        monkeypatch.setattr(ms_todo, "get_all_tasks", all_tasks)
        bot = FakeBot()
        bot_module._sent_unmarked.clear()
        loop.run_until_complete(bot_module.deliver_due(bot))
        assert [t for _, t, _ in bot.sent] == ["⏰ ручное"]
        loop.run_until_complete(reminder_sync.sync())
        rows = loop.run_until_complete(_rows())
        assert [(r[0], r[1]) for r in rows] == [("t1", soon)]
    finally:
        loop.run_until_complete(storage.close_db())
        loop.close()


# --- ревью 2 ---

def test_5xx_telegram_временная_ошибка(env):
    _due(env)
    env.bot.fail, env.bot.exc = 100, lambda: TelegramServerError(METHOD, "Bad Gateway")
    for _ in range(bot_module.REMINDER_MAX_ATTEMPTS + 2):
        env.run(bot_module.deliver_due(env.bot))
    assert len(env.run(_rows())) == 1


def test_перенос_туда_и_обратно_до_срабатывания_не_глушит_исходное_время(env):
    rid = env.run(storage.save_reminder(OWNER, utc(-timedelta(seconds=5)), "⏰ x", task_id="t1", source="sync"))
    old = env.run(_rows())[0][1]
    env.run(storage.set_reminder_fire_at(rid, utc(timedelta(hours=1))))   # 10:00 -> 11:00
    env.run(storage.set_reminder_fire_at(rid, old))                       # 11:00 -> 10:00
    env.run(bot_module.deliver_due(env.bot))
    assert len(env.bot.sent) == 1


def test_подавление_кэша_не_блокирует_новую_запись_с_той_же_парой(env):
    env.run(storage.save_reminder(OWNER, utc(-timedelta(seconds=5)), "⏰ x", task_id="t1", source="sync"))
    env.run(storage.delete_telegram_reminder_by_task("t1"))
    fire = utc(-timedelta(seconds=5))
    env.run(storage.save_reminder(OWNER, fire, "⏰ y", task_id="t1", source="button"))
    env.run(bot_module.deliver_due(env.bot))
    assert [t for _, t, _ in env.bot.sent] == ["⏰ y"]


def test_подавленная_пара_всё_ещё_не_воскресает_из_кэша(env):
    env.state["tasks"] = [task("t1", "A", timedelta(hours=2))]
    env.run(reminder_sync.sync())
    env.run(storage.delete_telegram_reminder_by_task("t1"))
    env.run(reminder_sync.sync())
    assert env.run(_rows()) == []


def test_готово_чистит_хвосты_как_complete_task_мини_аппа(env):
    key = env.run(storage.register_task_id("t1"))
    env.run(callbacks.cb_reminder_fire(FakeCallback(f"rem:done:{key}")))
    assert env.state["removed"] == ["t1"]


def test_синк_не_снимает_строку_созданную_во_время_запроса_задач(env):
    async def slow_tasks(list_id=None):
        # «+1 ч» успел поставить строку, пока синк ждал Graph
        await storage.save_reminder(OWNER, utc(timedelta(hours=1)), "⏰ x", task_id="t1", source="button")
        return []

    env.mp.setattr(ms_todo, "get_all_tasks", slow_tasks)
    env.run(reminder_sync.sync())
    assert len(env.run(_rows())) == 1


def test_усечение_привязано_к_ключу_кэша(env):
    ms_todo.invalidate_cache()
    forever = {"value": [{"id": "x"}], "@odata.nextLink": ms_todo.GRAPH_URL + "/next"}
    short = {"value": [{"id": "y"}]}

    async def fake_request(method, path, params=None, **kw):
        return forever if (params or {}).get("$filter") == "A" or "next" in path else short

    env.mp.setattr(ms_todo, "_request", fake_request)
    env.run(ms_todo.get_tasks("A"))
    assert ms_todo.last_fetch_truncated() is True
    env.run(ms_todo.get_tasks("B"))
    assert ms_todo.last_fetch_truncated() is False
    env.run(ms_todo.get_tasks("A"))                     # из кэша
    assert ms_todo.last_fetch_truncated() is True
    ms_todo.invalidate_cache()
