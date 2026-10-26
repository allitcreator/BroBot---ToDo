"""Фундамент перед мини-аппом: токены, LLM-клиент, проверка конфига.

Мини-апп шлёт запросы параллельно, поэтому рефреш токена обязан идти один
на всех, а протухший refresh-токен — превращаться в понятную ошибку, а не
в сырой «400 Bad Request».
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import google_calendar, llm, ms_todo  # noqa: E402


class _FakeTokenClient:
    """Подменяет httpx-клиент: считает POST на токен, отвечает с задержкой."""

    def __init__(self, status=200, body=None):
        self.posts = 0
        self.status = status
        self.body = body or {"access_token": "tok", "expires_in": 3600}
        self.is_closed = False

    async def post(self, url, data=None):
        self.posts += 1
        await asyncio.sleep(0.01)  # даём второму запросу встать в очередь
        return httpx.Response(self.status, json=self.body, request=httpx.Request("POST", url))


@pytest.fixture
def fresh_ms(monkeypatch):
    monkeypatch.setattr(ms_todo, "_access_token", None)
    monkeypatch.setattr(ms_todo, "_token_expires_at", 0)
    return ms_todo


@pytest.fixture
def fresh_google(monkeypatch):
    monkeypatch.setattr(google_calendar, "_access_token", None)
    monkeypatch.setattr(google_calendar, "_token_expires_at", 0)
    return google_calendar


@pytest.mark.parametrize("module_fixture", ["fresh_ms", "fresh_google"])
def test_parallel_requests_refresh_token_once(module_fixture, request, monkeypatch):
    module = request.getfixturevalue(module_fixture)
    fake = _FakeTokenClient()
    monkeypatch.setattr(module, "get_http_client", lambda: fake)

    async def run():
        return await asyncio.gather(*(module._get_token() for _ in range(5)))

    tokens = asyncio.run(run())
    assert tokens == ["tok"] * 5
    assert fake.posts == 1


def test_ms_expired_refresh_token_gives_clear_error(fresh_ms, monkeypatch):
    fake = _FakeTokenClient(status=400, body={
        "error": "invalid_grant",
        "error_description": "AADSTS700082: The refresh token has expired",
    })
    monkeypatch.setattr(ms_todo, "get_http_client", lambda: fake)

    with pytest.raises(ms_todo.AuthExpiredError) as exc:
        asyncio.run(ms_todo._get_token())
    assert "get_tokens.py" in str(exc.value)
    assert "AADSTS700082" in str(exc.value)


def test_google_expired_refresh_token_gives_clear_error(fresh_google, monkeypatch):
    fake = _FakeTokenClient(status=400, body={"error": "invalid_grant"})
    monkeypatch.setattr(google_calendar, "get_http_client", lambda: fake)

    with pytest.raises(google_calendar.AuthExpiredError) as exc:
        asyncio.run(google_calendar._get_token())
    assert "get_tokens.py" in str(exc.value)


def test_ms_keeps_rotated_refresh_token(fresh_ms, monkeypatch):
    fake = _FakeTokenClient(body={"access_token": "tok", "expires_in": 3600, "refresh_token": "new-rt"})
    monkeypatch.setattr(ms_todo, "get_http_client", lambda: fake)
    monkeypatch.setattr(ms_todo, "_current_refresh_token", None)

    asyncio.run(ms_todo._get_token())
    assert ms_todo._current_refresh_token == "new-rt"


def test_llm_client_has_short_timeout_and_one_retry():
    assert llm.client.max_retries == 1
    timeout = llm.client.timeout
    read = timeout.read if isinstance(timeout, httpx.Timeout) else timeout
    assert read is not None and read <= 60


def test_classify_failure_is_logged(monkeypatch, caplog):
    class _Broken:
        async def create(self, **kwargs):
            raise RuntimeError("openrouter down")

    class _Chat:
        completions = _Broken()

    monkeypatch.setattr(llm.client, "chat", _Chat())
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(llm.classify_message("купить молоко"))
    assert result["kind"] == "unclear"
    assert any("openrouter down" in (r.exc_text or "") + r.getMessage() for r in caplog.records)


_REQUIRED = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "TELEGRAM_USER_ID": 42,
    "OPENROUTER_API_KEY": "k",
    "MS_CLIENT_ID": "id",
    "MS_CLIENT_SECRET": "s",
    "MS_REFRESH_TOKEN": "rt",
    "MS_TODO_LIST_ID": "list",
}


def _set_config(monkeypatch, **overrides):
    values = dict(_REQUIRED, USE_WEBHOOK=False, WEBHOOK_SECRET="", WEBHOOK_HOST="")
    values.update(overrides)
    for k, v in values.items():
        monkeypatch.setattr(config, k, v)


def test_config_ok(monkeypatch):
    _set_config(monkeypatch)
    assert config.config_problems() == []


def test_config_missing_required(monkeypatch):
    _set_config(monkeypatch, MS_REFRESH_TOKEN=None, TELEGRAM_USER_ID=0)
    problems = " ".join(config.config_problems())
    assert "MS_REFRESH_TOKEN" in problems
    assert "TELEGRAM_USER_ID" in problems


def test_config_webhook_requires_secret(monkeypatch):
    _set_config(monkeypatch, USE_WEBHOOK=True, WEBHOOK_HOST="https://x", WEBHOOK_SECRET="")
    assert any("WEBHOOK_SECRET" in p for p in config.config_problems())

    _set_config(monkeypatch, USE_WEBHOOK=True, WEBHOOK_HOST="https://x", WEBHOOK_SECRET="short")
    assert any("WEBHOOK_SECRET" in p for p in config.config_problems())

    _set_config(monkeypatch, USE_WEBHOOK=True, WEBHOOK_HOST="https://x", WEBHOOK_SECRET="x" * 32)
    assert config.config_problems() == []


# --- кэш срезов задач ---

class _CountingRequest:
    def __init__(self):
        self.gets = 0
        self.writes = 0

    async def __call__(self, method, path, params=None, **kwargs):
        if method == "GET":
            self.gets += 1
            return {"value": [{"id": "t"}]}
        self.writes += 1
        return None


def test_срез_задач_кэшируется_и_сбрасывается_записью(monkeypatch):
    fake = _CountingRequest()
    monkeypatch.setattr(ms_todo, "_send", fake)
    ms_todo.invalidate_cache()

    async def scenario():
        await ms_todo.get_tasks("status ne 'completed'")
        await ms_todo.get_tasks("status ne 'completed'")
        assert fake.gets == 1, "второй вызов — из кэша"
        await ms_todo.update_task("t", title="новое")
        await ms_todo.get_tasks("status ne 'completed'")
        assert fake.gets == 2, "запись сбрасывает кэш"

    asyncio.run(scenario())


def test_кэш_истекает(monkeypatch):
    fake = _CountingRequest()
    monkeypatch.setattr(ms_todo, "_send", fake)
    ms_todo.invalidate_cache()
    now = [1000.0]
    monkeypatch.setattr(ms_todo.time, "monotonic", lambda: now[0])

    async def scenario():
        await ms_todo.get_tasks("status ne 'completed'")
        now[0] += ms_todo.CACHE_TTL + 1
        await ms_todo.get_tasks("status ne 'completed'")

    asyncio.run(scenario())
    assert fake.gets == 2
