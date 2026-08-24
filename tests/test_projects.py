"""Бриф записывается в заметку MS Todo и читается оттуда же — значит
render_body/parse_body обязаны быть обратны друг другу. Это здесь и проверяется.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import projects  # noqa: E402


BRIEF = {
    "title": "Бот для проектов",
    "why": "Идеи теряются в переписке.",
    "what": "Бот превращает мысль в бриф.\nMVP — только текст.",
    "how": "Интервью из вопросов, ответ пишется в MS Todo.",
    "benefit": "Мне: меньше забытых замыслов.",
    "metrics": "Через месяц: 3 проекта с отмеченными шагами.",
    "risks": "Интервью надоест на третьем проекте.",
    "steps": ["Сделать классификатор", "Написать интервью", "Записать проект"],
}


REQUIRED_KEYS = [k for k in projects.SECTION_KEYS if k not in projects.OPTIONAL_SECTIONS]


def test_round_trip_возвращает_те_же_секции():
    parsed = projects.parse_body(projects.render_body(BRIEF))
    for key in REQUIRED_KEYS:
        assert parsed[key] == BRIEF[key], key


def test_многострочная_секция_не_рвётся():
    parsed = projects.parse_body(projects.render_body(BRIEF))
    assert parsed["what"].splitlines() == ["Бот превращает мысль в бриф.", "MVP — только текст."]


def test_пустое_поле_становится_заглушкой():
    body = projects.render_body({"title": "X", "why": "", "what": "Суть"})
    parsed = projects.parse_body(body)
    assert parsed["why"] == projects.EMPTY
    assert parsed["what"] == "Суть"


def test_порушенная_заметка_не_роняет_парсер():
    parsed = projects.parse_body("просто текст без заголовков")
    assert parsed == {key: "" for key in projects.SECTION_KEYS}
    assert projects.body_is_valid("просто текст без заголовков") is False
    assert projects.body_is_valid(projects.render_body(BRIEF)) is True


def test_текст_до_первого_заголовка_игнорируется():
    body = "мусор сверху\n\n" + projects.render_body(BRIEF)
    assert projects.parse_body(body)["why"] == BRIEF["why"]


def test_неизвестные_заголовки_пропускаются():
    body = projects.render_body(BRIEF) + "\n\n## Придумано руками\nчто-то ещё"
    parsed = projects.parse_body(body)
    assert parsed["risks"] == BRIEF["risks"]
    assert "что-то ещё" not in "".join(parsed.values())


def test_md_содержит_заголовок_секции_и_нумерованный_план():
    md = projects.render_md(BRIEF)
    assert md.startswith("# Бот для проектов")
    for key, title in projects.SECTIONS:
        if key in projects.OPTIONAL_SECTIONS:
            continue
        assert f"## {title}" in md
    assert "1. Сделать классификатор" in md
    assert "3. Записать проект" in md


def test_md_без_шагов_не_рисует_пустой_план():
    md = projects.render_md({**BRIEF, "steps": []})
    assert "## План" not in md


def test_превью_без_markdown_разметки():
    # parse_mode=None у бота: звёздочки утекли бы в текст как есть
    preview = projects.format_brief_preview(BRIEF)
    assert "**" not in preview
    assert "Бот для проектов" in preview
    assert "1. Сделать классификатор" in preview


def test_превью_режет_длинную_секцию():
    preview = projects.format_brief_preview({**BRIEF, "why": "я" * 500}, max_section=100)
    assert "я" * 101 not in preview
    assert "…" in preview
