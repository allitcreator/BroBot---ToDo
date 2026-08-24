"""Сессия интервью по проекту: старт, ходы, финиш, запись в MS Todo.

Живёт отдельным модулем, потому что вход в интервью есть и из сообщения
(классификатор решил «проект»), и из кнопок превью — оба хендлера зовут
одни и те же функции.

Состояние сессии лежит в user_states, как и остальные диалоги бота:
переживает рестарт, новых таблиц не заводит.
"""
import config
from db import storage
from services import interview, projects
from handlers.keyboards import interview_session_kb, brief_review_kb, project_actions_kb

STATE_SESSION = "interview_in_session"
STATE_REVIEW = "brief_review"
STATE_UNCLEAR = "unclear_choice"
STATE_FILL = "project_fill_waiting"

TELEGRAM_LIMIT = 4000


def cut_text(text: str) -> str:
    return text if len(text) <= TELEGRAM_LIMIT else text[:TELEGRAM_LIMIT].rstrip() + "…"


async def start_interview(bot, chat_id: int, user_id: int, seed_text: str):
    """Первый вопрос интервью. История пустая, мысль уходит в системный контекст."""
    await bot.send_chat_action(chat_id, "typing")
    turn = await interview.next_turn(seed_text, [])

    if turn["failed"]:
        await bot.send_message(chat_id, f"❌ {turn['question']}")
        return

    history = [{"role": "assistant", "content": turn["question"]}]
    await storage.set_state(user_id, STATE_SESSION, {
        "seed_text": seed_text,
        "history": history,
        "turn_count": 1,
        "coverage": turn["coverage"],
    })
    await bot.send_message(
        chat_id,
        f"🚀 Разбираем идею. Отвечай текстом или голосом.\n\n{turn['question']}",
        reply_markup=interview_session_kb(),
    )


async def handle_answer(message, state_data: dict, text: str):
    """Очередной ответ пользователя в интервью."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    seed_text = state_data.get("seed_text", "")
    history = list(state_data.get("history") or [])
    turn_count = int(state_data.get("turn_count") or 0)

    history.append({"role": "user", "content": text})

    # Потолок вопросов: дальше не спрашиваем, собираем что есть.
    if turn_count >= config.INTERVIEW_MAX_QUESTIONS:
        await message.answer(
            f"Достигнут потолок в {config.INTERVIEW_MAX_QUESTIONS} вопросов — собираю бриф."
        )
        await finish_interview(message.bot, chat_id, user_id, seed_text, history)
        return

    await message.bot.send_chat_action(chat_id, "typing")
    turn = await interview.next_turn(seed_text, history)

    if turn["failed"]:
        # Ход не состоялся: лимит не тратим, ответ пользователя сохраняем.
        await storage.set_state(user_id, STATE_SESSION, {
            "seed_text": seed_text,
            "history": history,
            "turn_count": turn_count,
            "coverage": state_data.get("coverage") or interview.empty_coverage(),
        })
        await message.answer(f"❌ {turn['question']}")
        return

    if turn["done"]:
        await finish_interview(message.bot, chat_id, user_id, seed_text, history)
        return

    history.append({"role": "assistant", "content": turn["question"]})
    await storage.set_state(user_id, STATE_SESSION, {
        "seed_text": seed_text,
        "history": history,
        "turn_count": turn_count + 1,
        "coverage": turn["coverage"],
    })
    await message.answer(turn["question"], reply_markup=interview_session_kb())


async def finish_interview(bot, chat_id: int, user_id: int, seed_text: str, history: list[dict]):
    """Компиляция брифа и показ его на подтверждение."""
    await bot.send_chat_action(chat_id, "typing")
    brief = await interview.compile_brief(seed_text, history)
    await show_brief(bot, chat_id, user_id, brief)


async def show_brief(bot, chat_id: int, user_id: int, brief: dict):
    """Показ собранного брифа с кнопками сохранения."""
    await storage.set_state(user_id, STATE_REVIEW, {"brief": brief})
    await bot.send_message(
        chat_id,
        cut_text(projects.format_brief_preview(brief)),
        reply_markup=brief_review_kb(),
    )


async def save_project(bot, chat_id: int, user_id: int, brief: dict):
    """Запись проекта в список «Проекты» MS Todo."""
    await bot.send_chat_action(chat_id, "typing")
    try:
        result = await projects.create_project(brief)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Не удалось создать проект: {e}")
        return

    await storage.clear_state(user_id)

    key = await storage.register_task_id(result["task"]["id"])
    steps_count = len(brief.get("steps") or [])
    text = f"✅ Проект создан: {brief.get('title')}"
    if steps_count:
        text += f"\n📍 Шагов в плане: {steps_count}"
    if result.get("attachment_error"):
        text += f"\n⚠️ MD-файл не приложился: {result['attachment_error']}"

    research_enabled = await storage.get_research_enabled(user_id)
    await bot.send_message(chat_id, text, reply_markup=project_actions_kb(key, research_enabled))
