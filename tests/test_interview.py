"""Интервью не имеет права падать на кривом ответе модели: пользователь уже
потратил на диалог несколько минут. Здесь проверяются именно деградации.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import interview  # noqa: E402


FULL_COVERAGE = {d: True for d in interview.DIMENSIONS}


def test_нормальный_ход_разбирается():
    raw = json.dumps({
        "question": "Что случится, если не сделать?",
        "coverage": {"why": True, "what": True, "how": False,
                     "benefit": False, "demand": False, "cost": False},
        "done": False,
    })
    turn = interview.parse_turn(raw)
    assert turn["question"] == "Что случится, если не сделать?"
    assert turn["coverage"]["why"] is True
    assert turn["done"] is False
    assert turn["failed"] is False


def test_невалидный_json_даёт_безопасный_fallback():
    turn = interview.parse_turn("не json вовсе")
    assert turn["failed"] is True
    assert turn["done"] is False
    assert turn["question"]


def test_done_без_полного_покрытия_не_принимается():
    # Модель любит объявить победу раньше времени — тогда probe останутся непродавленными
    raw = json.dumps({"question": "", "coverage": {"why": True}, "done": True})
    turn = interview.parse_turn(raw)
    assert turn["done"] is False
    assert turn["question"], "должен быть задан хоть какой-то вопрос"


def test_полное_покрытие_закрывает_интервью():
    raw = json.dumps({"question": "", "coverage": FULL_COVERAGE, "done": True})
    turn = interview.parse_turn(raw)
    assert turn["done"] is True
    assert turn["failed"] is False


def test_покрытие_без_probe_не_считается_полным():
    coverage = {**FULL_COVERAGE, "demand": False, "cost": False}
    assert interview.coverage_done(coverage) is False
    assert interview.missing_dimensions(coverage) == ["нужность", "затраты"]


def test_мусор_в_coverage_читается_как_false():
    raw = json.dumps({"question": "Зачем?", "coverage": "сломалось", "done": False})
    turn = interview.parse_turn(raw)
    assert turn["coverage"] == interview.empty_coverage()


def test_бриф_заполняет_все_поля():
    raw = json.dumps({
        "title": "Проект",
        "why": "Затем",
        "what": "Вот это",
        "how": "Так",
        "benefit": "Мне",
        "metrics": "Три штуки",
        "risks": "Надоест",
        "steps": ["Шаг раз", "Шаг два"],
    })
    brief = interview.parse_brief(raw, "исходная мысль")
    assert brief["title"] == "Проект"
    assert brief["steps"] == ["Шаг раз", "Шаг два"]
    assert brief["risks"] == "Надоест"


def test_нераскрытые_поля_помечаются():
    brief = interview.parse_brief(json.dumps({"title": "Проект"}), "мысль")
    assert brief["why"] == "(не раскрыто)"
    assert brief["steps"] == []


def test_шаги_словарями_разворачиваются_в_строки():
    raw = json.dumps({"title": "П", "steps": [{"step": "Сделать раз"}, "Сделать два", 42]})
    brief = interview.parse_brief(raw, "мысль")
    assert brief["steps"] == ["Сделать раз", "Сделать два"]


def test_битый_бриф_даёт_заголовок_из_исходной_мысли():
    brief = interview.parse_brief("}{", "Хочу сделать бота для заметок")
    assert brief["title"] == "Хочу сделать бота для заметок"
    assert brief["what"] == "Хочу сделать бота для заметок"


def test_длинный_заголовок_обрезается():
    brief = interview.parse_brief(json.dumps({"title": "т" * 200}), "мысль")
    assert len(brief["title"]) <= 60

    fallback = interview.fallback_title("д" * 200)
    assert len(fallback) <= 60
