"""Разведка: разбор ссылок из ответа Sonar и сборка секции брифа.

Формат annotations задаёт провайдер, поэтому парсер читает и словари, и
объекты SDK, и не спотыкается о мусор — справка без ссылок всё равно полезна.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import projects, research  # noqa: E402


class _Citation:
    def __init__(self, url, title):
        self.url = url
        self.title = title


class _Annotation:
    def __init__(self, url, title):
        self.url_citation = _Citation(url, title)


class _Message:
    def __init__(self, annotations):
        self.annotations = annotations


def test_ссылки_читаются_из_объектов_sdk():
    msg = _Message([_Annotation("https://a.ru/x", "Статья A"), _Annotation("https://b.ru/y", "Статья B")])
    sources = research.extract_sources(msg)
    assert sources == [
        {"title": "Статья A", "url": "https://a.ru/x"},
        {"title": "Статья B", "url": "https://b.ru/y"},
    ]


def test_ссылки_читаются_из_словарей():
    msg = _Message([{"type": "url_citation", "url_citation": {"url": "https://a.ru", "title": "A"}}])
    assert research.extract_sources(msg) == [{"title": "A", "url": "https://a.ru"}]


def test_дубли_и_мусор_отбрасываются():
    msg = _Message([
        _Annotation("https://a.ru", "A"),
        _Annotation("https://a.ru", "A ещё раз"),
        _Annotation("", "без ссылки"),
        {"type": "нечто"},
    ])
    assert research.extract_sources(msg) == [{"title": "A", "url": "https://a.ru"}]


def test_количество_источников_ограничено():
    msg = _Message([_Annotation(f"https://site{i}.ru", f"S{i}") for i in range(20)])
    assert len(research.extract_sources(msg)) == research.MAX_SOURCES


def test_нет_annotations_не_роняет():
    assert research.extract_sources(_Message(None)) == []
    assert research.extract_sources(object()) == []


def test_секция_содержит_выжимку_и_ссылки():
    section = research.render_section({
        "summary": "Есть готовые mmWave-датчики.",
        "sources": [{"title": "Обзор", "url": "https://example.com/1"}],
    })
    assert "Есть готовые mmWave-датчики." in section
    assert "Источники:" in section
    assert "https://example.com/1" in section


def test_секция_без_ссылок_не_рисует_пустой_заголовок():
    section = research.render_section({"summary": "Ничего похожего не нашлось.", "sources": []})
    assert "Источники" not in section


def test_запрос_собирается_из_заполненных_полей():
    query = research._format_query("Свет по датчикам", {
        "what": "LLM решает, включать ли свет",
        "how": "(не раскрыто)",
        "why": "",
    })
    assert "Свет по датчикам" in query
    assert "LLM решает" in query
    assert "(не раскрыто)" not in query


# --- секция «Что уже есть» в брифе ---

def test_пустая_разведка_не_попадает_в_заметку():
    body = projects.render_body({"why": "Затем", "what": "Вот это"})
    assert "Что уже есть" not in body
    assert "## Зачем" in body


def test_заполненная_разведка_рендерится_и_читается_обратно():
    brief = {"why": "Затем", "research": "Есть Aqara FP2.\nИсточники:\n- обзор: https://x.ru"}
    parsed = projects.parse_body(projects.render_body(brief))
    assert parsed["research"] == brief["research"]


def test_разведка_попадает_в_md_и_превью():
    brief = {"why": "Затем", "research": "Есть Aqara FP2."}
    assert "## Что уже есть" in projects.render_md(brief)
    assert "Что уже есть" in projects.format_brief_preview(brief)
    assert "Что уже есть" not in projects.format_brief_preview({"why": "Затем"})
