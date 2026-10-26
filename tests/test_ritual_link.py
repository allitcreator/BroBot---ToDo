"""Ссылка на ритуал в мини-аппе: параметр в query web_app-кнопки."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from handlers import commands  # noqa: E402


@pytest.mark.parametrize("base, expected", [
    ("https://todo.alxforbot.online/", "https://todo.alxforbot.online/?ritual=morning"),
    ("https://todo.alxforbot.online/?v=2", "https://todo.alxforbot.online/?v=2&ritual=morning"),
    ("https://todo.alxforbot.online/?ritual=old", "https://todo.alxforbot.online/?ritual=morning"),
])
def test_ссылка_на_ритуал(base, expected):
    assert commands.ritual_url(base, "morning") == expected


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.from_user = SimpleNamespace(id=1)
        self.answers = []

    async def answer(self, text, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))


def _send(text, monkeypatch):
    monkeypatch.setattr(config, "MINIAPP_URL", "https://todo.alxforbot.online/")
    msg = FakeMessage(text)
    asyncio.run(commands.cmd_ritual(msg, SimpleNamespace(args=text.partition(" ")[2] or None)))
    return msg.answers[-1]


@pytest.mark.parametrize("arg", ["morning", "evening", "test"])
def test_команда_ritual_шлёт_кнопку_с_параметром(arg, monkeypatch):
    _, markup = _send(f"/ritual {arg}", monkeypatch)
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == f"https://todo.alxforbot.online/?ritual={arg}"


def test_команда_ritual_без_параметра_подсказывает(monkeypatch):
    text, markup = _send("/ritual", monkeypatch)
    assert markup is None and "morning" in text
