"""Классификатор с замоканной LLM: важно не качество промпта, а то, что
любой мусор от модели превращается в 'unclear', а не в исключение — иначе
сообщение пользователя потеряется.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import llm  # noqa: E402


class _FakeCompletions:
    def __init__(self, content=None, exc=None):
        self.content = content
        self.exc = exc

    async def create(self, **kwargs):
        if self.exc:
            raise self.exc

        class _Msg:
            content = self.content

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


class _FakeClient:
    def __init__(self, content=None, exc=None):
        completions = _FakeCompletions(content, exc)
        self.chat = type("chat", (), {"completions": completions})()


def _classify(monkeypatch_content=None, exc=None) -> dict:
    original = llm.client
    llm.client = _FakeClient(monkeypatch_content, exc)
    try:
        return asyncio.run(llm.classify_message("текст"))
    finally:
        llm.client = original


def test_задача_распознаётся():
    result = _classify(json.dumps({"kind": "task", "reason": "конкретное действие"}))
    assert result["kind"] == "task"
    assert result["reason"] == "конкретное действие"


def test_проект_распознаётся():
    result = _classify(json.dumps({"kind": "project", "reason": "замысел без срока"}))
    assert result["kind"] == "project"


def test_неизвестный_kind_схлопывается_в_unclear():
    result = _classify(json.dumps({"kind": "идея", "reason": "?"}))
    assert result["kind"] == "unclear"


def test_невалидный_json_не_роняет_классификатор():
    result = _classify("сломанный ответ")
    assert result["kind"] == "unclear"


def test_ошибка_сети_не_роняет_классификатор():
    result = _classify(exc=RuntimeError("сеть недоступна"))
    assert result["kind"] == "unclear"
    assert result["reason"]


def test_reason_всегда_строка():
    result = _classify(json.dumps({"kind": "task", "reason": {"вложенный": "мусор"}}))
    assert isinstance(result["reason"], str)
