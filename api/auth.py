"""Авторизация мини-аппа по initData.

Telegram подписывает данные пользователя ключом, производным от токена бота.
Проверка подписи плюс сверка user.id с владельцем — единственное, что
отделяет постороннего от задач. Алгоритм и тесты перенесены из splitbot
(`bot_spliwise/backend/app/api/auth.py`).
"""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

# Сколько живёт подпись. Перехваченный initData даёт полный доступ к задачам,
# пока подпись свежая, поэтому срок короткий. Открытый дольше мини-апп
# получит 401, и фронт попросит открыть его заново.
MAX_AUTH_AGE_SECONDS = 2 * 60 * 60

_HASH_RE = re.compile(r"[0-9a-f]{64}")


class InitDataError(ValueError):
    pass


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """Проверяет подпись и возвращает разобранные поля (user — словарём)."""
    if not init_data:
        raise InitDataError("пустая строка initData")
    try:
        fields = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError as exc:
        raise InitDataError("initData не разбирается") from exc

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("в initData нет подписи")
    # compare_digest падает с TypeError на не-ASCII строке — проверяем формат заранее.
    if not _HASH_RE.fullmatch(received_hash):
        raise InitDataError("подпись в неверном формате")

    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    # Постоянное время сравнения: обычное == подсказывало бы подбор побайтно.
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("подпись не сходится")

    auth_date = fields.get("auth_date", "")
    if not auth_date.isdigit():
        raise InitDataError("нет даты авторизации")
    age = time.time() - int(auth_date)
    if age > MAX_AUTH_AGE_SECONDS:
        raise InitDataError("подпись просрочена, открой мини-апп заново")
    if age < -300:
        raise InitDataError("дата авторизации из будущего")

    try:
        fields["user"] = json.loads(fields.get("user") or "")
    except json.JSONDecodeError as exc:
        raise InitDataError("в initData нет пользователя") from exc
    return fields
