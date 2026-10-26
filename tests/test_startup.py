"""Старт бота переживает сетевой сбой до api.telegram.org.

2026-10-26 при выкатке Telegram на секунду сбросил соединение: on_startup
упал на первом же вызове, процесс завис живым, порт не слушал, а docker
не перезапускал — бот молчал, пока его не перезапустили руками.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot as bot_module  # noqa: E402
import config  # noqa: E402
from db import storage  # noqa: E402


async def _no_sleep(_):
    return None


def test_повтор_после_разового_сбоя(monkeypatch):
    monkeypatch.setattr(bot_module.asyncio, "sleep", _no_sleep)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("reset by peer")
        return "ok"

    assert asyncio.run(bot_module._with_retries(flaky, "test")) == "ok"
    assert len(calls) == 3


def test_исчерпанные_попытки_пробрасывают_ошибку(monkeypatch):
    monkeypatch.setattr(bot_module.asyncio, "sleep", _no_sleep)

    async def broken():
        raise ConnectionResetError("reset by peer")

    with pytest.raises(ConnectionResetError):
        asyncio.run(bot_module._with_retries(broken, "test", attempts=3))


class _FakeBot:
    def __init__(self, webhook_ok=True, commands_ok=True):
        self.webhook_ok = webhook_ok
        self.commands_ok = commands_ok
        self.webhook_set = False

    async def set_my_commands(self, commands):
        if not self.commands_ok:
            raise ConnectionResetError("reset")

    async def set_chat_menu_button(self, **kw):
        raise ConnectionResetError("reset")

    async def set_webhook(self, url, secret_token=None):
        if not self.webhook_ok:
            raise ConnectionResetError("reset")
        self.webhook_set = True


@pytest.fixture
def startup_env(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(config, "MINIAPP_URL", "https://todo.example/")

    async def no_loop(bot):
        return None

    monkeypatch.setattr(bot_module, "_reminder_loop", no_loop)
    yield
    asyncio.run(storage.close_db())


def test_необязательные_вызовы_не_мешают_вебхуку(startup_env):
    fake = _FakeBot(commands_ok=False)
    asyncio.run(bot_module.on_startup(fake))
    assert fake.webhook_set


def test_без_вебхука_процесс_выходит_а_не_висит(startup_env, monkeypatch):
    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(bot_module.os, "_exit", fake_exit)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(bot_module.on_startup(_FakeBot(webhook_ok=False)))
    assert exc.value.code == 1
