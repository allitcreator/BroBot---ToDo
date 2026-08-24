"""Проекты: бриф живёт в заметке задачи MS Todo, шаги — в её чеклисте.

Источник правды — сам MS Todo, никакого локального зеркала. Отсюда формат:
заметка задачи собирается из фиксированных markdown-заголовков, и она же
разбирается обратно, когда бриф надо дополнить.

`render_body` и `parse_body` — чистые функции и обратны друг другу, на них
держится весь цикл чтения-записи; они же покрыты тестами.
"""
import config
from services import ms_todo

# Порядок секций в заметке. Ключ брифа → заголовок.
SECTIONS = [
    ("why", "Зачем"),
    ("what", "Что"),
    ("how", "Как"),
    ("benefit", "Польза"),
    ("metrics", "Метрики успеха"),
    ("risks", "Открытые риски"),
    ("research", "Что уже есть"),
]

SECTION_KEYS = [key for key, _ in SECTIONS]

# Секции, которые не рисуются заглушкой, пока пусты: их заполняет разведка,
# а не интервью, и пустой заголовок в заметке только мешает.
OPTIONAL_SECTIONS = {"research"}

EMPTY = "(не раскрыто)"

MD_ATTACHMENT_NAME = "brief.md"


def render_body(brief: dict) -> str:
    """Собирает заметку задачи из брифа."""
    parts = []
    for key, title in SECTIONS:
        value = (brief.get(key) or "").strip()
        if not value and key in OPTIONAL_SECTIONS:
            continue
        parts.append(f"## {title}\n{value or EMPTY}")
    return "\n\n".join(parts)


def parse_body(text: str) -> dict:
    """Разбирает заметку обратно в поля брифа.

    Заголовки ищутся построчно, всё до первого заголовка игнорируется.
    Если структура порушена руками — вернутся пустые поля, и вызывающий код
    решит, что делать (перезаписать или отдать LLM на пересборку).
    """
    result = {key: "" for key in SECTION_KEYS}
    title_to_key = {title.lower(): key for key, title in SECTIONS}

    current: str | None = None
    buffer: list[str] = []

    def flush():
        if current is not None:
            result[current] = "\n".join(buffer).strip()

    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            flush()
            current = title_to_key.get(stripped[3:].strip().lower())
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    flush()

    return result


def body_is_valid(text: str) -> bool:
    """True, если в заметке нашлась хотя бы одна известная секция."""
    parsed = parse_body(text)
    return any(parsed.get(key) for key in SECTION_KEYS)


def render_md(brief: dict, steps: list[str] | None = None) -> str:
    """MD-файл, который прикладывается вложением к задаче."""
    lines = [f"# {brief.get('title') or 'Без названия'}", ""]
    for key, title in SECTIONS:
        value = (brief.get(key) or "").strip()
        if not value and key in OPTIONAL_SECTIONS:
            continue
        lines.append(f"## {title}")
        lines.append(value or EMPTY)
        lines.append("")
    steps = steps if steps is not None else (brief.get("steps") or [])
    if steps:
        lines.append("## План")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_brief_preview(brief: dict, steps: list[str] | None = None, max_section: int = 300) -> str:
    """Текст брифа для показа в Telegram.

    Без markdown: бот шлёт сообщения с parse_mode=None, разметка утекла бы
    в текст звёздочками.
    """
    lines = [f"🚀 {brief.get('title') or 'Без названия'}", ""]
    for key, title in SECTIONS:
        value = (brief.get(key) or "").strip()
        if not value and key in OPTIONAL_SECTIONS:
            continue
        value = value or EMPTY
        if len(value) > max_section:
            value = value[:max_section].rstrip() + "…"
        lines.append(f"▪️ {title}")
        lines.append(value)
        lines.append("")
    steps = steps if steps is not None else (brief.get("steps") or [])
    if steps:
        lines.append("📍 План")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
    return "\n".join(lines).strip()


def _list_id() -> str | None:
    return config.MS_PROJECTS_LIST_ID


async def create_project(brief: dict) -> dict:
    """Создаёт проект: задача в списке «Проекты» + шаги чеклистом + MD вложением.

    Задача создаётся без даты — у проекта нет срока. Падение на вложении не
    считается провалом: бриф и шаги уже записаны, вложение вторично.
    """
    if not _list_id():
        raise Exception(
            "Не задан MS_PROJECTS_LIST_ID. Создай список скриптом create_projects_list.py "
            "и впиши id в .env"
        )

    steps = brief.get("steps") or []
    task = await ms_todo.create_task(
        title=brief.get("title") or "Без названия",
        description=render_body(brief),
        subtasks=steps,
        list_id=_list_id(),
    )

    attachment_error = None
    try:
        await ms_todo.replace_attachment(
            task["id"], MD_ATTACHMENT_NAME, render_md(brief, steps).encode("utf-8"), _list_id(),
        )
    except Exception as e:
        attachment_error = str(e)

    return {"task": task, "attachment_error": attachment_error}


async def get_project(task_id: str) -> dict:
    """Читает проект из MS Todo: бриф из заметки, шаги из чеклиста."""
    task = await ms_todo.get_task(task_id, _list_id())
    body = (task.get("body") or {}).get("content") or ""
    brief = parse_body(body)
    brief["title"] = task.get("title") or ""
    items = await ms_todo.get_checklist_items(task_id, _list_id())
    return {
        "task": task,
        "brief": brief,
        "body_valid": body_is_valid(body),
        "steps": [
            {"text": i.get("displayName", ""), "checked": bool(i.get("isChecked"))}
            for i in items
        ],
    }


async def append_to_section(task_id: str, key: str, addition: str) -> dict:
    """Дописывает абзац в одну секцию брифа и пересобирает вложение.

    Заметку MS Todo нельзя дополнить частично — читаем свой же текст,
    меняем нужную секцию, пишем всё обратно.
    """
    if key not in SECTION_KEYS:
        raise ValueError(f"Неизвестная секция брифа: {key}")

    project = await get_project(task_id)
    brief = project["brief"]

    current = (brief.get(key) or "").strip()
    if current in ("", EMPTY):
        brief[key] = addition.strip()
    else:
        brief[key] = f"{current}\n{addition.strip()}"

    await ms_todo.update_task_body(task_id, render_body(brief), _list_id())

    steps = [s["text"] for s in project["steps"]]
    try:
        await ms_todo.replace_attachment(
            task_id, MD_ATTACHMENT_NAME, render_md(brief, steps).encode("utf-8"), _list_id(),
        )
    except Exception:
        pass

    return brief


async def set_section(task_id: str, key: str, value: str) -> dict:
    """Перезаписывает одну секцию целиком (в отличие от append_to_section)."""
    if key not in SECTION_KEYS:
        raise ValueError(f"Неизвестная секция брифа: {key}")

    project = await get_project(task_id)
    brief = project["brief"]
    brief[key] = value.strip()

    await ms_todo.update_task_body(task_id, render_body(brief), _list_id())

    steps = [s["text"] for s in project["steps"]]
    try:
        await ms_todo.replace_attachment(
            task_id, MD_ATTACHMENT_NAME, render_md(brief, steps).encode("utf-8"), _list_id(),
        )
    except Exception:
        pass

    return brief


async def list_projects() -> list[dict]:
    if not _list_id():
        raise Exception("Не задан MS_PROJECTS_LIST_ID — список «Проекты» не подключён")
    return await ms_todo.get_all_tasks(_list_id())


async def delete_project(task_id: str):
    await ms_todo.delete_task(task_id, _list_id())
