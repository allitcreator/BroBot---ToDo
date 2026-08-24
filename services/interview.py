"""Интервью по идее проекта: вопрос за вопросом, на выходе бриф и план шагов.

Два публичных вызова:

- `next_turn(seed_text, history)` — следующий вопрос + покрытие измерений.
- `compile_brief(seed_text, history)` — финальный бриф со списком шагов.

Методология перенесена из BroBot-memory (спринт 10, live-протестирована):
4 обязательных измерения (зачем / что / как / польза) и 2 персональных
probe-челленджа (demand / cost) — они зашиты в промпт глобально, потому что
это известные слепые пятна пользователя: преувеличивать полезность и
недооценивать затраты.

Любая ошибка LLM гасится безопасным fallback: интервью не должно падать,
иначе теряется набранный диалог.
"""
import json
import logging

from openai import APIStatusError

from services.llm import client, _llm_error

logger = logging.getLogger(__name__)

MODEL = "google/gemini-3-flash-preview"

# Измерения покрытия. Первые четыре — обязательные, последние два — probe.
DIMENSIONS = ("why", "what", "how", "benefit", "demand", "cost")

DIMENSION_LABELS = {
    "why": "зачем",
    "what": "что",
    "how": "как",
    "benefit": "польза",
    "demand": "нужность",
    "cost": "затраты",
}

_NEXT_TURN_SYSTEM = """Ты проводишь короткое рабочее интервью с пользователем, чтобы превратить его сырую идею в проработанный замысел проекта.

Это не опрос, а разговор с челленджем: если ответ размытый — переспроси, не давай отделаться общими словами.

СНАЧАЛА определи по тексту, что перед тобой, и веди интервью соответственно:
- ЛИЧНОЕ — инструмент, эксперимент или доработка быта для себя (домашняя автоматизация, свой скрипт, «поставить и попробовать»). Признаки: «мне», «дома», «попробовать», нет внешних пользователей.
- ПРОДУКТ — то, чем будут пользоваться другие люди: сервис, приложение, бизнес.
Определи это сам по признакам и не трать на выяснение отдельный ход. Спроси явно только если признаков правда нет — и только один раз: если ответ пришёл не про это, прими рабочее предположение и веди интервью дальше.

Собери понимание по шести измерениям:
1. why — зачем это нужно, что не так сейчас, что случится, если НЕ сделать.
2. what — суть замысла одним предложением и граница минимальной версии.
3. how — как это работает: механика, из чего собирается, что уже есть.
4. benefit — кому именно и что улучшится, по каким признакам это будет видно.
5. demand — проверка, что польза не преувеличена. Вопросы РАЗНЫЕ в зависимости от типа:
   - для ЛИЧНОГО: как часто ты реально сталкиваешься с этой болью? чем не устраивает готовое решение? будешь ли пользоваться этим через месяц или это разовый интерес? Про чужой спрос, аудиторию и деньги НЕ спрашивай — это личная затея, и то, что никто её не просил, здесь нормально.
   - для ПРОДУКТА: кто конкретно тебя об этом просил? сколько раз ты сам пользовался этим вручную? нужно ли это кому-то кроме тебя? нет ли готового решения?
6. cost — проверка, что затраты не занижены. Дави прямо в любом случае: сколько часов реально, не «за выходные»? что здесь самое сложное? потянет ли железо или бюджет? какую часть ты уже делал руками, откуда уверенность в оценке?

Измерения 5 и 6 обязательны: продави каждое хотя бы одним прямым вопросом, даже если пользователь уже кажется убедительным.

Глубина под масштаб. Мелкая затея («поставить и попробовать», вечер работы) — хватает 4-6 вопросов, дальше не тяни. Крупный замысел — копай глубже. Не задавай вопрос ради галочки, если ответ на него уже очевиден из сказанного.

Правила:
- Один вопрос за ход. Короткий, конкретный, по-русски, на «ты».
- Учитывай сказанное, не повторяй вопрос, на который уже ответили.
- Ответ размытый — переспроси, прежде чем отмечать измерение раскрытым.
- Но не заедай: дословно один и тот же вопрос дважды не задавай НИКОГДА. Если человек ушёл от ответа или ответил не про то — зайди с другой стороны, а на третий раз прими как есть, отметь измерение раскрытым и иди дальше. Несказанное попадёт в риски при сборке брифа.
- Отмечай измерение true ТОЛЬКО когда оно реально раскрыто.
- Когда раскрыты все шесть — done: true и question: "".
- Никаких видимых ролей и меток: не пиши «WHY:», «Probe:», «CTO:». Только сам вопрос.
- Без вводных вежливостей вроде «отличный вопрос» — сухо и по делу.

Отвечай строго валидным JSON без markdown:
{"question": "<вопрос или пустая строка>", "coverage": {"why":bool,"what":bool,"how":bool,"benefit":bool,"demand":bool,"cost":bool}, "done": bool}
"""

_COMPILE_SYSTEM = """Ты только что провёл интервью по идее проекта. Собери разговор в бриф.

Не копируй ответы дословно — переформулируй в ясные конкретные фразы, как для внутреннего документа. Если по полю информации мало — напиши строку "(не раскрыто)", НЕ выдумывай.

Поля JSON:
- title: название проекта, до 60 символов, без кавычек.
- why: зачем это нужно, что не так сейчас (1-3 предложения).
- what: суть замысла и граница минимальной версии.
- how: как это работает — механика, из чего собирается.
- benefit: кому нужно и что улучшится.
- metrics: по каким признакам через месяц будет понятно, что сработало.
- risks: что может пойти не так, включая то, что вскрылось в вопросах про нужность и затраты. Если пользователь ушёл от оценки трудозатрат или не смог сказать, как часто ему это реально нужно, — так и напиши, это главный риск. Для личной затеи «никто кроме автора этого не просил» риском НЕ считается: пиши то, что действительно ей грозит — не потянет железо, заброшу после первой недели, готовое решение закрывает 90% случаев.
- steps: массив из 5-10 строк — конкретные шаги реализации по порядку. Каждый шаг — законченное действие, начинается с глагола, без нумерации внутри строки.

Отвечай строго валидным JSON без markdown, все поля кроме steps — строки.
"""


_DRAFT_SYSTEM = """Пользователь скинул сырую идею и не хочет отвечать на вопросы — разбери её за него.

Заполни те же поля брифа сам, опираясь на текст и на здравый смысл. Это черновик, который человек потом поправит, поэтому:
- Где ты достраиваешь за него — начинай фразу со слова «Предположительно» и пиши свой вариант, а не «(не раскрыто)». Пустых полей быть не должно.
- Границу минимальной версии предлагай узкую: что можно собрать за один вечер и уже проверить на себе.
- В how назови конкретный стек или инструменты, а не абстракции.
- В risks трезво оцени два места, где такие затеи обычно рушатся: реальные трудозатраты против ожидаемых и то, что готовое решение может закрыть задачу дешевле. Если задача личная, «никто об этом не просил» риском не считается.
- В metrics предложи признак, по которому через месяц станет ясно, пользуется человек этим или забросил.
- steps: 5-10 шагов, каждый — законченное действие с глагола, в порядке выполнения.

Поля: title (до 60 символов), why, what, how, benefit, metrics, risks — строки; steps — массив строк.
Отвечай строго валидным JSON без markdown."""


async def draft_brief(seed_text: str) -> dict:
    """Черновой разбор идеи без интервью: модель заполняет бриф гипотезами."""
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _DRAFT_SYSTEM},
                {"role": "user", "content": seed_text.strip() or "(пусто)"},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content or "{}"
    except Exception as e:
        logger.warning("interview.draft_brief: %s", e)
        return fallback_brief(seed_text)

    return parse_brief(raw, seed_text)


def empty_coverage() -> dict:
    return {d: False for d in DIMENSIONS}


def coverage_done(coverage: dict) -> bool:
    return all(coverage.get(d) for d in DIMENSIONS)


def missing_dimensions(coverage: dict) -> list[str]:
    """Человекочитаемый список нераскрытых измерений."""
    return [DIMENSION_LABELS[d] for d in DIMENSIONS if not coverage.get(d)]


def _messages(system: str, seed_text: str, history: list[dict]) -> list[dict]:
    system_with_context = (
        f"{system}\n\n=== Изначальная мысль пользователя ===\n{seed_text.strip() or '(пусто)'}"
    )
    messages = [{"role": "system", "content": system_with_context}]
    for h in history:
        role = h.get("role")
        content = h.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    return messages


async def next_turn(seed_text: str, history: list[dict]) -> dict:
    """Следующий ход интервью.

    Возвращает {"question": str, "coverage": dict, "done": bool, "failed": bool}.
    `failed` = True, когда модель не ответила: сессию в этом случае не двигаем,
    чтобы пустой ход не съедал лимит вопросов.
    """
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=_messages(_NEXT_TURN_SYSTEM, seed_text, history),
            response_format={"type": "json_object"},
            temperature=0.5,
            max_tokens=512,
        )
        raw = response.choices[0].message.content or "{}"
    except APIStatusError as e:
        logger.warning("interview.next_turn: %s", _llm_error(e))
        return {
            "question": "Модель сейчас не отвечает. Напиши ещё раз через минуту.",
            "coverage": empty_coverage(),
            "done": False,
            "failed": True,
        }
    except Exception as e:
        logger.warning("interview.next_turn: %s", e)
        return {
            "question": "Модель сейчас не отвечает. Напиши ещё раз через минуту.",
            "coverage": empty_coverage(),
            "done": False,
            "failed": True,
        }

    return parse_turn(raw)


def parse_turn(raw: str) -> dict:
    """Разбор ответа LLM с защитой от мусора. Чистая функция."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("interview: невалидный JSON хода: %r", (raw or "")[:200])
        return {
            "question": "Не понял ответ модели. Сформулируй мысль ещё раз.",
            "coverage": empty_coverage(),
            "done": False,
            "failed": True,
        }

    if not isinstance(data, dict):
        return {
            "question": "Не понял ответ модели. Сформулируй мысль ещё раз.",
            "coverage": empty_coverage(),
            "done": False,
            "failed": True,
        }

    raw_coverage = data.get("coverage")
    coverage = empty_coverage()
    if isinstance(raw_coverage, dict):
        for d in DIMENSIONS:
            coverage[d] = bool(raw_coverage.get(d, False))

    question = data.get("question")
    question = question.strip() if isinstance(question, str) else ""
    done = bool(data.get("done", False)) or coverage_done(coverage)

    # Модель сказала «готово», но вопроса нет и покрытие неполное — не верим.
    if done and not coverage_done(coverage):
        done = False
    if not done and not question:
        return {
            "question": "Расскажи чуть подробнее — что здесь главное?",
            "coverage": coverage,
            "done": False,
            "failed": False,
        }

    return {"question": question, "coverage": coverage, "done": done, "failed": False}


async def compile_brief(seed_text: str, history: list[dict]) -> dict:
    """Финальный бриф. При ошибке — заготовка с заголовком из исходного текста."""
    messages = _messages(_COMPILE_SYSTEM, seed_text, history)
    messages.append({
        "role": "user",
        "content": "Собери итоговый бриф по нашему разговору в JSON, как описано в системном промпте.",
    })

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content or "{}"
    except Exception as e:
        logger.warning("interview.compile_brief: %s", e)
        return fallback_brief(seed_text)

    return parse_brief(raw, seed_text)


def parse_brief(raw: str, seed_text: str) -> dict:
    """Разбор брифа с защитой от мусора. Чистая функция."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("interview: невалидный JSON брифа: %r", (raw or "")[:200])
        return fallback_brief(seed_text)

    if not isinstance(data, dict):
        return fallback_brief(seed_text)

    def _text(key: str) -> str:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "(не раскрыто)"

    raw_steps = data.get("steps")
    steps: list[str] = []
    if isinstance(raw_steps, list):
        for s in raw_steps:
            if isinstance(s, str) and s.strip():
                steps.append(s.strip())
            elif isinstance(s, dict):
                # Модель иногда отдаёт [{"step": "..."}] — вытаскиваем текст.
                for v in s.values():
                    if isinstance(v, str) and v.strip():
                        steps.append(v.strip())
                        break

    title = data.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else fallback_title(seed_text)

    return {
        "title": title[:60],
        "why": _text("why"),
        "what": _text("what"),
        "how": _text("how"),
        "benefit": _text("benefit"),
        "metrics": _text("metrics"),
        "risks": _text("risks"),
        "steps": steps,
    }


def fallback_title(seed_text: str) -> str:
    """Заголовок из первой непустой строки исходного текста."""
    for line in (seed_text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:57].rstrip() + "…" if len(stripped) > 60 else stripped
    return "Без названия"


def fallback_brief(seed_text: str) -> dict:
    """Пустой бриф — пользователь увидит проект и дозаполнит руками."""
    return {
        "title": fallback_title(seed_text),
        "why": "(не раскрыто)",
        "what": (seed_text or "").strip() or "(не раскрыто)",
        "how": "(не раскрыто)",
        "benefit": "(не раскрыто)",
        "metrics": "(не раскрыто)",
        "risks": "(не раскрыто)",
        "steps": [],
    }
