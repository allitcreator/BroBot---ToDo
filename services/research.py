"""Разведка по проекту: поиск в интернете через Perplexity Sonar.

Зачем отдельная модель: у Gemini веб-поиск через OpenRouter не подмешивает
результаты в контекст (проверено — приходит ответ по памяти, без источников),
а `perplexity/sonar` возвращает и выжимку, и ссылки в `annotations`.
Один запрос стоит около половины цента.

Отвечает на два вопроса, которые интервью иначе берёт из памяти модели:
что уже существует и во что упрёмся технически.
"""
import logging

from services.llm import client

logger = logging.getLogger(__name__)

MODEL = "perplexity/sonar"
MAX_SOURCES = 6

_SYSTEM = """Ты собираешь короткую справку по чужому замыслу. Не хвали идею и не пересказывай её.

Ответь строго по двум пунктам, каждый — 2-4 предложения:
1. Что уже есть: готовые продукты, сервисы, железо или библиотеки, которые закрывают эту задачу целиком или частично. Указывай конкретные названия и порядок цен.
2. Обо что споткнётся: технические ограничения, требования к железу, известные грабли, порядок трудозатрат.

Пиши по-русски, сухо, без вводных. Если по какому-то пункту достоверного не нашлось — так и напиши одной строкой, не выдумывай."""


def _format_query(title: str, brief: dict) -> str:
    parts = [f"Замысел: {title}"]
    for key, label in (("what", "суть"), ("how", "как устроено"), ("why", "зачем")):
        value = (brief.get(key) or "").strip()
        if value and value != "(не раскрыто)":
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


async def research(title: str, brief: dict | None = None) -> dict:
    """Возвращает {"summary": str, "sources": [{"title","url"}], "error": str|None}."""
    query = _format_query(title, brief or {})

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": query},
            ],
            temperature=0.2,
            max_tokens=700,
        )
    except Exception as e:
        logger.warning("research: %s", e)
        return {"summary": "", "sources": [], "error": str(e)}

    message = response.choices[0].message
    summary = (message.content or "").strip()
    return {"summary": summary, "sources": extract_sources(message), "error": None}


def extract_sources(message) -> list[dict]:
    """Достаёт ссылки из annotations ответа Sonar. Формат у провайдера может
    измениться, поэтому читаем защитно — без источников справка всё равно полезна."""
    sources: list[dict] = []
    annotations = getattr(message, "annotations", None) or []
    for a in annotations:
        citation = a.get("url_citation") if isinstance(a, dict) else getattr(a, "url_citation", None)
        if citation is None:
            continue
        url = citation.get("url") if isinstance(citation, dict) else getattr(citation, "url", "")
        title = citation.get("title") if isinstance(citation, dict) else getattr(citation, "title", "")
        if not url:
            continue
        if any(s["url"] == url for s in sources):
            continue
        sources.append({"title": (title or url)[:80], "url": url})
        if len(sources) >= MAX_SOURCES:
            break
    return sources


def render_section(result: dict) -> str:
    """Текст для секции брифа «Что уже есть»: выжимка плюс ссылки списком."""
    parts = [result.get("summary", "").strip()]
    sources = result.get("sources") or []
    if sources:
        parts.append("")
        parts.append("Источники:")
        for s in sources:
            parts.append(f"- {s['title']}: {s['url']}")
    return "\n".join(p for p in parts if p is not None).strip()
