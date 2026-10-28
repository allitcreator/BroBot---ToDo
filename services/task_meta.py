"""Служебный блок в заметке задачи MS To Do: порядок на день, исполнитель,
рекомендации для агента.

    <текст заметки>

    ── BroBot ──
    Порядок: 2 на 28.10.2026
    Исполнитель: агент
    Рекомендации:
    сначала логи
    ── /BroBot ──

Блок — от последнего открывающего маркера до закрывающего (нет закрывающего —
до конца). Текст над блоком и после него сохраняется побайтно, в том числе
`\\r\\n` из приложения To Do. Поля разбираются только до «Рекомендации:»,
дальше всё — текст рекомендаций. Чужой текст (форварды, описание от LLM)
перед записью проходит `neutralize`, чтобы не подделать блок.

Те же правила разбора у ассистента в `../todo_and_run`; общие случаи —
`tests/fixtures/task_meta_cases.json`.
"""
import re
from dataclasses import dataclass
from datetime import date

OPEN = "── BroBot ──"
CLOSE = "── /BroBot ──"
ADVICE_LIMIT = 2000
EXECUTORS = ("agent", "self")

_EXECUTOR_BY_WORD = {"агент": "agent", "сам": "self"}
_WORD_BY_EXECUTOR = {v: k for k, v in _EXECUTOR_BY_WORD.items()}
_ORDER = re.compile(r"Порядок:\s*(\d{1,3})\s+на\s+(\d{2})\.(\d{2})\.(\d{4})")
_ADVICE = "Рекомендации:"
# Разделитель между текстом и блоком; при разборе снимается ровно он.
_SEP = "\n\n"


@dataclass(frozen=True)
class Meta:
    order: int | None = None
    order_date: date | None = None
    executor: str | None = None  # agent | self
    advice: str | None = None

    def order_on(self, day: date) -> int | None:
        """Номер действует только в свой день."""
        return self.order if self.order is not None and self.order_date == day else None

    def is_empty(self) -> bool:
        return (self.order is None or self.order_date is None) and not self.executor and not (self.advice or "").strip()


def neutralize(text: str | None) -> str | None:
    """Строки-маркеры в чужом тексте — в безобидный вид, остальное как есть."""
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    return "".join(line.replace("──", "--") if line.strip() in (OPEN, CLOSE) else line for line in lines)


def _locate(content: str):
    """(текст до блока, строки блока, текст после блока) или None."""
    lines = content.splitlines(keepends=True)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)

    start = None
    for i, line in enumerate(lines):
        if line.strip() == OPEN:
            start = i
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].strip() == CLOSE), None)

    head = content[: offsets[start]]
    if head.endswith("\r\n\r\n"):
        head = head[:-4]
    elif head.endswith(_SEP):
        head = head[: -len(_SEP)]

    body = [line.rstrip("\r\n") for line in lines[start + 1 : len(lines) if end is None else end]]
    if end is None:
        tail = ""
    else:
        close = lines[end]
        tail = close[len(close.rstrip("\r\n")) :] + content[offsets[end] + len(close) :]
    return head, body, tail


def _parse(body: list[str]) -> Meta:
    order = order_date = executor = None
    advice: list[str] | None = None
    for line in body:
        if advice is not None:
            advice.append(line)
            continue
        s = line.strip()
        if s == _ADVICE:
            advice = []
        elif m := _ORDER.fullmatch(s):
            try:
                order_date = date(int(m[4]), int(m[3]), int(m[2]))
                order = int(m[1])
            except ValueError:
                pass
        elif s.startswith("Исполнитель:"):
            executor = _EXECUTOR_BY_WORD.get(s.split(":", 1)[1].strip().lower())
    text = "\n".join(advice or []).strip()
    return Meta(order=order, order_date=order_date, executor=executor, advice=text or None)


def split(content: str | None) -> tuple[str, Meta]:
    """Заметка без блока и данные блока."""
    content = content or ""
    located = _locate(content)
    if located is None:
        return content, Meta()
    head, body, tail = located
    return head + tail if head else tail.lstrip("\r\n"), _parse(body)


def _render(meta: Meta) -> str:
    lines = [OPEN]
    if meta.order is not None and meta.order_date is not None:
        lines.append(f"Порядок: {meta.order} на {meta.order_date:%d.%m.%Y}")
    if meta.executor:
        lines.append(f"Исполнитель: {_WORD_BY_EXECUTOR[meta.executor]}")
    advice = (meta.advice or "").strip()
    if advice:
        lines += [_ADVICE, neutralize(advice.replace("\r\n", "\n"))]
    lines.append(CLOSE)
    return "\n".join(lines)


def apply(content: str | None, meta: Meta) -> str:
    """Заметка с новым блоком; пустой `meta` убирает блок целиком."""
    content = content or ""
    located = _locate(content)
    head, tail = (content, "") if located is None else (located[0], located[2])
    if meta.is_empty():
        if located is None:
            return content
        return head + tail if head else tail.lstrip("\r\n")
    return head + (_SEP if head else "") + _render(meta) + tail
