import json
import base64
from datetime import date, datetime
from zoneinfo import ZoneInfo
from openai import AsyncOpenAI, APIStatusError
import config

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=config.OPENROUTER_API_KEY,
)

SYSTEM_PROMPT = """Ты ассистент, который парсит текстовые сообщения и извлекает задачи для менеджера задач.

Верни JSON объект со следующими полями:
- title: string — название задачи. Используй слова пользователя, не переформулируй. Если одно действие — весь текст в title. Если несколько независимых действий — объедини кратко.
- due_date: string | null — дата в формате YYYY-MM-DD. Если дата не указана — null.
- due_time: string | null — время в формате HH:MM (24ч). Если время не указано — null.
- duration_minutes: int | null — длительность в минутах. Если не указана — null.
- subtasks: string[] | null — подзадачи, если в сообщении есть явный список (нумерованный, буллеты, перечисление через запятую после двоеточия). Иначе null.
- confidence: "high" | "low" — насколько уверен, что это задача (low = похоже на обычное сообщение, не задачу).
- is_event: boolean — true если это событие для календаря (встреча, приём врача, созвон, запись куда-то, презентация). false если это обычная задача (купить, проверить, посмотреть, сделать).

Правила:
- Не сокращай title без необходимости
- Относительные даты (сегодня, завтра, в пятницу) переводи в абсолютные на основе текущей даты
- Если задача явно не требует напоминания — confidence = low
- Ответ ТОЛЬКО в виде JSON, без markdown блоков
"""


async def parse_task(text: str, today: str | None = None) -> dict:
    if today is None:
        today = config.local_today().isoformat()

    user_content = f"Сегодня: {today}\n\nСообщение: {text}"

    try:
        response = await client.chat.completions.create(
            model="google/gemini-3-flash-preview",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=512,
        )
    except APIStatusError as e:
        if e.status_code == 402:
            raise Exception("Закончились кредиты OpenRouter. Пополни баланс на openrouter.ai") from e
        raise Exception(f"Ошибка LLM: {e.status_code}") from e

    raw = response.choices[0].message.content
    data = json.loads(raw)

    # Если дата не указана — подставляем сегодня
    if not data.get("due_date"):
        data["due_date"] = today

    return data


async def transcribe_voice(audio_bytes: bytes) -> str:
    """Транскрибирует аудио через Gemini мультимодальный."""
    audio_b64 = base64.b64encode(audio_bytes).decode()
    data_uri = f"data:audio/ogg;base64,{audio_b64}"

    try:
        response = await client.chat.completions.create(
            model="google/gemini-3-flash-preview",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Транскрибируй это голосовое сообщение. Верни ТОЛЬКО текст, без пояснений."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
            temperature=0,
            max_tokens=256,
        )
    except APIStatusError as e:
        if e.status_code == 402:
            raise Exception("Закончились кредиты OpenRouter. Пополни баланс на openrouter.ai") from e
        raise Exception(f"Ошибка транскрипции: {e.status_code}") from e

    return response.choices[0].message.content.strip()


def _normalize_decimal(text: str) -> str:
    """Заменяет русский формат дробей: 1,5 → 1.5"""
    import re
    return re.sub(r'(\d),(\d)', r'\1.\2', text)


async def parse_calendar_details(text: str, today: str | None = None) -> dict:
    """Парсит время и длительность из произвольного текста."""
    if today is None:
        today = config.local_today().isoformat()

    text = _normalize_decimal(text)

    try:
        response = await client.chat.completions.create(
            model="google/gemini-3-flash-preview",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Извлеки из текста время и длительность события. "
                        "Верни JSON: {\"due_time\": \"HH:MM\" | null, \"duration_minutes\": int | null}. "
                        "Примеры длительности: '1.5 часа' = 90, '2 часа' = 120, '30 минут' = 30. "
                        f"Сегодня: {today}. Ответ ТОЛЬКО в виде JSON без markdown."
                    ),
                },
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=256,
        )
    except APIStatusError as e:
        if e.status_code == 402:
            raise Exception("Закончились кредиты OpenRouter. Пополни баланс на openrouter.ai") from e
        raise Exception(f"Ошибка LLM: {e.status_code}") from e

    return json.loads(response.choices[0].message.content)


async def parse_reminder_offset(text: str, due_date: str, due_time: str | None = None) -> str | None:
    """Парсит текст пользователя о времени напоминания.
    Возвращает ISO datetime string в UTC (без timezone суффикса) или None."""
    user_tz = ZoneInfo(config.USER_TIMEZONE)
    now_local = datetime.now(user_tz)
    utc_offset_hours = int(now_local.utcoffset().total_seconds() // 3600)
    tz_label = f"UTC{'+' if utc_offset_hours >= 0 else ''}{utc_offset_hours}"

    today_str = now_local.strftime("%Y-%m-%d")
    now_time_str = now_local.strftime("%H:%M")

    if due_time:
        task_info = f"Задача запланирована на {due_date} в {due_time} (местное время, {tz_label})."
        examples = (
            f"Примеры: \"за 15 минут\" = за 15 мин до {due_time}, \"за час\" = за 60 мин до {due_time}, "
            f"\"точно в 15:00\" = {due_date}T15:00:00."
        )
    else:
        task_info = f"Задача запланирована на {due_date} (время не указано). Сейчас {today_str} {now_time_str} ({tz_label})."
        examples = (
            f"Примеры: \"сегодня в 13:00\" = {today_str}T13:00:00, "
            f"\"завтра в 10:00\" = дата завтра T10:00:00, \"через 2 часа\" = текущее время + 2 часа."
        )

    prompt = (
        f"{task_info} "
        f"Пользователь хочет напоминание: \"{text}\"\n"
        f"Верни JSON с точным временем напоминания в местном времени ({tz_label}): "
        f"{{\"fire_at\": \"YYYY-MM-DDTHH:MM:SS\"}}. "
        f"{examples} Только JSON."
    )
    try:
        response = await client.chat.completions.create(
            model="google/gemini-3-flash-preview",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=64,
        )
    except APIStatusError as e:
        if e.status_code == 402:
            raise Exception("Закончились кредиты OpenRouter.") from e
        raise Exception(f"Ошибка LLM: {e.status_code}") from e

    data = json.loads(response.choices[0].message.content)
    fire_at_str = data.get("fire_at")
    if not fire_at_str:
        return None

    dt_local = datetime.fromisoformat(fire_at_str).replace(tzinfo=user_tz)
    dt_utc = dt_local.astimezone(ZoneInfo("UTC"))
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S")
