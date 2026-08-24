from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.types import CallbackQuery
import config
from db import storage
from db.storage import resolve_task_id, register_task_id, remove_task_header
from services import ms_todo, google_calendar
from handlers.keyboards import (
    confirm_task_kb, calendar_ask_kb, settings_kb,
    confirm_done_kb, confirm_delete_kb, task_actions_kb,
    task_more_kb, reminder_where_kb, reminder_ask_kb,
    project_actions_kb, project_fields_kb, project_confirm_delete_kb,
)
from handlers.utils import ask_reminder_if_needed, format_task_preview, rebuild_task_text, format_fire_at, format_event_info
from handlers import project_flow

router = Router()

user_filter = F.from_user.func(lambda u: u.id == config.TELEGRAM_USER_ID)


# --- Подтверждение создания задачи ---

@router.callback_query(F.data == "confirm:create", user_filter)
async def cb_confirm_create(callback: CallbackQuery):
    user_id = callback.from_user.id
    task = await storage.get_pending_task(user_id)
    if not task:
        await callback.answer("Задача не найдена")
        return

    await callback.message.edit_reply_markup()
    await storage.delete_pending_task(user_id)

    try:
        created = await ms_todo.create_task(
            title=task["title"],
            due_date=task["due_date"],
            due_time=task.get("due_time"),
            subtasks=task.get("subtasks"),
            description=task.get("description"),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при создании задачи: {e}")
        await callback.answer()
        return

    key = await register_task_id(created["id"])
    await callback.message.answer(
        f"✅ Задача создана: {task['title']}",
        reply_markup=task_actions_kb(key),
    )
    await callback.answer()

    if not task.get("is_event"):
        await ask_reminder_if_needed(
            callback.bot, callback.message.chat.id, user_id,
            created["id"], task["title"], task["due_date"], task.get("due_time"),
        )
        return

    cal_data = {
        "task_id": created["id"],
        "title": task["title"],
        "due_date": task["due_date"],
        "due_time": task.get("due_time"),
        "duration_minutes": task.get("duration_minutes"),
    }

    has_time = bool(task.get("due_time"))
    has_duration = bool(task.get("duration_minutes"))

    if has_time and has_duration:
        await storage.set_state(user_id, "cal_ask", cal_data)
        await callback.message.answer(
            "📅 Добавить событие в Google Calendar?",
            reply_markup=calendar_ask_kb(),
        )
    elif has_time:
        await storage.set_state(user_id, "cal_waiting_duration", cal_data)
        await callback.message.answer(
            "📅 Добавить в Google Calendar?\nЕсли да — напиши длительность.",
            reply_markup=calendar_ask_kb(),
        )
    else:
        await storage.set_state(user_id, "cal_waiting_time_duration", cal_data)
        await callback.message.answer(
            "📅 Добавить в Google Calendar?\nЕсли да — напиши время и длительность.",
            reply_markup=calendar_ask_kb(),
        )


@router.callback_query(F.data == "confirm:edit", user_filter)
async def cb_confirm_edit(callback: CallbackQuery):
    user_id = callback.from_user.id
    await storage.set_state(user_id, "editing_pending_title")
    await callback.message.edit_reply_markup()
    await callback.message.answer("✏️ Напиши новое название задачи:")
    await callback.answer()


@router.callback_query(F.data == "confirm:cancel", user_filter)
async def cb_confirm_cancel(callback: CallbackQuery):
    user_id = callback.from_user.id
    await storage.delete_pending_task(user_id)
    await storage.clear_state(user_id)
    await callback.message.edit_reply_markup()
    await callback.message.answer("❌ Отменено.")
    await callback.answer()


# --- Проекты: вход в интервью ---

@router.callback_query(F.data == "confirm:project", user_filter)
async def cb_confirm_project(callback: CallbackQuery):
    """«Это проект» на превью задачи: задача не создаётся, уходим в интервью.

    Решение принимается до создания задачи намеренно — перенести уже созданную
    задачу между списками MS Graph не умеет.
    """
    user_id = callback.from_user.id
    task = await storage.get_pending_task(user_id)
    seed_text = ""
    if task:
        seed_text = task.get("source_text") or task.get("title") or ""
    await storage.delete_pending_task(user_id)
    await storage.clear_state(user_id)

    await callback.message.edit_reply_markup()
    await callback.answer()

    if not seed_text:
        await callback.message.answer("❌ Нечего разбирать — напиши идею заново.")
        return

    await project_flow.start_interview(callback.bot, callback.message.chat.id, user_id, seed_text)


@router.callback_query(F.data.startswith("unclear:"), user_filter)
async def cb_unclear_choice(callback: CallbackQuery):
    """Развилка, когда классификатор не уверен."""
    from services import llm

    user_id = callback.from_user.id
    choice = callback.data.split(":", 1)[1]
    state, state_data = await storage.get_state(user_id)
    seed_text = (state_data or {}).get("seed_text", "") if state == project_flow.STATE_UNCLEAR else ""
    await storage.clear_state(user_id)

    if choice == "cancel" or not seed_text:
        await callback.message.edit_reply_markup()
        await callback.answer()
        if choice == "cancel":
            await callback.message.answer("❌ Отменено.")
        else:
            # Состояние перетёрлось следующим сообщением — исходного текста уже нет
            await callback.message.answer("⏳ Это сообщение устарело, напиши мысль заново.")
        return

    if choice == "project":
        await callback.message.edit_reply_markup()
        await callback.answer()
        await project_flow.start_interview(callback.bot, callback.message.chat.id, user_id, seed_text)
        return

    # choice == "task": разбираем как обычную задачу
    await callback.bot.send_chat_action(callback.message.chat.id, "typing")
    try:
        task = await llm.parse_task(seed_text)
    except Exception as e:
        await callback.message.edit_reply_markup()
        await callback.answer()
        await callback.message.answer(f"❌ Ошибка при разборе задачи: {e}")
        return
    task["source_text"] = seed_text
    await storage.save_pending_task(user_id, task)

    confirm_mode = await storage.get_confirm_mode(user_id)
    if confirm_mode == "off":
        # Создаём сразу — тем же обработчиком, что и кнопка «Создать»,
        # чтобы не заводить второй путь создания задачи.
        await cb_confirm_create(callback)
        return

    await callback.message.edit_reply_markup()
    await callback.answer()
    await callback.message.answer(
        f"📋 Создать задачу?\n\n{format_task_preview(task)}",
        reply_markup=confirm_task_kb(),
    )


# --- Проекты: интервью ---

@router.callback_query(F.data == "interview:enough", user_filter)
async def cb_interview_enough(callback: CallbackQuery):
    user_id = callback.from_user.id
    state, state_data = await storage.get_state(user_id)
    if state != project_flow.STATE_SESSION or not state_data:
        await callback.answer("Интервью уже закрыто")
        return

    await callback.message.edit_reply_markup()
    await callback.answer("Собираю бриф")
    await project_flow.finish_interview(
        callback.bot, callback.message.chat.id, user_id,
        state_data.get("seed_text", ""), state_data.get("history") or [],
    )


@router.callback_query(F.data == "interview:draft", user_filter)
async def cb_interview_draft(callback: CallbackQuery):
    """«Разбери сам»: модель заполняет бриф гипотезами, вопросов больше не будет."""
    from services import interview

    user_id = callback.from_user.id
    state, state_data = await storage.get_state(user_id)
    if state != project_flow.STATE_SESSION or not state_data:
        await callback.answer("Интервью уже закрыто")
        return

    await callback.message.edit_reply_markup()
    await callback.answer("Разбираю сам")
    await callback.bot.send_chat_action(callback.message.chat.id, "typing")

    brief = await interview.draft_brief(state_data.get("seed_text", ""))
    await project_flow.show_brief(callback.bot, callback.message.chat.id, user_id, brief)


@router.callback_query(F.data == "interview:to_task", user_filter)
async def cb_interview_to_task(callback: CallbackQuery):
    """Классификатор ошибся в сторону проекта: возвращаемся в обычный флоу задачи."""
    from services import llm

    user_id = callback.from_user.id
    state, state_data = await storage.get_state(user_id)
    if state != project_flow.STATE_SESSION or not state_data:
        await callback.answer("Интервью уже закрыто")
        return

    seed_text = state_data.get("seed_text", "")
    await storage.clear_state(user_id)
    await callback.message.edit_reply_markup()
    await callback.bot.send_chat_action(callback.message.chat.id, "typing")

    try:
        task = await llm.parse_task(seed_text)
    except Exception as e:
        await callback.answer()
        await callback.message.answer(f"❌ Ошибка при разборе задачи: {e}")
        return

    task["source_text"] = seed_text
    await storage.save_pending_task(user_id, task)

    confirm_mode = await storage.get_confirm_mode(user_id)
    if confirm_mode == "off":
        await cb_confirm_create(callback)
        return

    await callback.answer()
    await callback.message.answer(
        f"📋 Создать задачу?\n\n{format_task_preview(task)}",
        reply_markup=confirm_task_kb(),
    )


@router.callback_query(F.data == "interview:cancel", user_filter)
async def cb_interview_cancel(callback: CallbackQuery):
    user_id = callback.from_user.id
    await storage.clear_state(user_id)
    await callback.message.edit_reply_markup()
    await callback.message.answer("❌ Интервью отменено, ничего не сохранено.")
    await callback.answer()


@router.callback_query(F.data == "brief:save", user_filter)
async def cb_brief_save(callback: CallbackQuery):
    user_id = callback.from_user.id
    state, state_data = await storage.get_state(user_id)
    if state != project_flow.STATE_REVIEW or not state_data:
        await callback.answer("Бриф не найден")
        return

    await callback.message.edit_reply_markup()
    await callback.answer()
    await project_flow.save_project(
        callback.bot, callback.message.chat.id, user_id, state_data.get("brief") or {},
    )


@router.callback_query(F.data == "brief:cancel", user_filter)
async def cb_brief_cancel(callback: CallbackQuery):
    user_id = callback.from_user.id
    await storage.clear_state(user_id)
    await callback.message.edit_reply_markup()
    await callback.message.answer("❌ Проект не сохранён.")
    await callback.answer()


# --- Проекты: работа с готовым проектом ---

@router.callback_query(F.data.startswith("proj:open:"), user_filter)
async def cb_project_open(callback: CallbackQuery):
    from services import projects

    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    try:
        project = await projects.get_project(task_id)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return

    steps = [
        f"{'✅' if s['checked'] else '⬜️'} {s['text']}" for s in project["steps"]
    ]
    text = projects.format_brief_preview(project["brief"], steps=[])
    if steps:
        text += "\n\n📍 План\n" + "\n".join(steps)
    if not project["body_valid"]:
        text += "\n\n⚠️ Заметка не разбирается по секциям — правилась вручную?"

    research_enabled = await storage.get_research_enabled(callback.from_user.id)
    await callback.message.answer(
        project_flow.cut_text(text),
        reply_markup=project_actions_kb(key, research_enabled),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("proj:research:"), user_filter)
async def cb_project_research(callback: CallbackQuery):
    """Разведка: ищет в интернете готовые решения и технические ограничения."""
    from services import projects, research

    user_id = callback.from_user.id
    if not await storage.get_research_enabled(user_id):
        await callback.answer("Разведка выключена в /settings", show_alert=True)
        return

    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    await callback.answer("Ищу в интернете")
    await callback.bot.send_chat_action(callback.message.chat.id, "typing")

    try:
        project = await projects.get_project(task_id)
        result = await research.research(project["brief"].get("title", ""), project["brief"])
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка разведки: {e}")
        return

    if result["error"] or not result["summary"]:
        await callback.message.answer(f"❌ Разведка не удалась: {result['error'] or 'пустой ответ'}")
        return

    section = research.render_section(result)
    try:
        await projects.set_section(task_id, "research", section)
    except Exception as e:
        await callback.message.answer(f"⚠️ Нашёл, но не смог записать в проект: {e}")

    await callback.message.answer(
        project_flow.cut_text(f"🔎 Что уже есть\n\n{section}"),
        reply_markup=project_actions_kb(key, True),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("proj:fill:"), user_filter)
async def cb_project_fill(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    await callback.message.edit_reply_markup(reply_markup=project_fields_kb(key))
    await callback.answer("Что дополнить?")


@router.callback_query(F.data.startswith("proj:field:"), user_filter)
async def cb_project_field(callback: CallbackQuery):
    from services import projects

    user_id = callback.from_user.id
    _, _, index, key = callback.data.split(":", 3)
    task_id = await resolve_task_id(key) or key

    try:
        field_key, field_title = projects.SECTIONS[int(index)]
    except (ValueError, IndexError):
        await callback.answer("Неизвестная секция", show_alert=True)
        return

    prompt_msg = await callback.message.answer(f"✏️ Что дописать в «{field_title}»?")
    await storage.set_state(user_id, project_flow.STATE_FILL, {
        "task_id": task_id,
        "field": field_key,
        "key": key,
        "chat_id": callback.message.chat.id,
        "prompt_msg_id": prompt_msg.message_id,
    })
    research_enabled = await storage.get_research_enabled(user_id)
    await callback.message.edit_reply_markup(reply_markup=project_actions_kb(key, research_enabled))
    await callback.answer()


@router.callback_query(F.data.startswith("proj:back:"), user_filter)
async def cb_project_back(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    research_enabled = await storage.get_research_enabled(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=project_actions_kb(key, research_enabled))
    await callback.answer()


@router.callback_query(F.data.startswith("proj:delete:"), user_filter)
async def cb_project_delete(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    await callback.message.edit_reply_markup(reply_markup=project_confirm_delete_kb(key))
    await callback.answer("Удалить проект?")


@router.callback_query(F.data.startswith("proj:delete_yes:"), user_filter)
async def cb_project_delete_yes(callback: CallbackQuery):
    from services import projects

    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    try:
        await projects.delete_project(task_id)
        await callback.message.delete()
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    await callback.answer("🗑 Проект удалён")


# --- Календарь ---

@router.callback_query(F.data == "cal:yes", user_filter)
async def cb_cal_yes(callback: CallbackQuery):
    user_id = callback.from_user.id
    state, state_data = await storage.get_state(user_id)

    await callback.message.edit_reply_markup()
    await callback.answer()

    if not state_data:
        return

    if state == "cal_ask":
        try:
            event = await google_calendar.create_event(
                title=state_data["title"],
                due_date=state_data["due_date"],
                due_time=state_data["due_time"],
                duration_minutes=state_data["duration_minutes"],
            )
            await storage.save_calendar_link(state_data["task_id"], event["id"])
            await callback.message.answer("✅ Событие добавлено в Google Calendar")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        await ask_reminder_if_needed(
            callback.bot, callback.message.chat.id, user_id,
            state_data["task_id"], state_data["title"], state_data["due_date"], state_data.get("due_time"),
        )

    elif state == "cal_waiting_duration":
        await callback.message.answer("Напиши длительность (например: 1 час, 30 минут):")

    elif state == "cal_waiting_time_duration":
        await callback.message.answer("Напиши время и длительность (например: в 14:00, 1 час):")


@router.callback_query(F.data == "cal:no", user_filter)
async def cb_cal_no(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, state_data = await storage.get_state(user_id)
    await storage.clear_state(user_id)
    await callback.message.edit_reply_markup()
    await callback.answer("Пропущено")
    if state_data:
        await ask_reminder_if_needed(
            callback.bot, callback.message.chat.id, user_id,
            state_data.get("task_id", ""), state_data.get("title", ""),
            state_data.get("due_date", ""), state_data.get("due_time"),
        )


# --- Основные действия с задачами ---

async def _cleanup_task_extras(task_id: str):
    """Удаляет Telegram-напоминание и событие Google Calendar, связанные с задачей."""
    try:
        await storage.delete_telegram_reminder_by_task(task_id)
    except Exception:
        pass
    try:
        await ms_todo.remove_reminder(task_id)
    except Exception:
        pass
    event_id = await storage.get_calendar_link(task_id)
    if event_id:
        try:
            await google_calendar.delete_event(event_id)
        except Exception:
            pass
        await storage.delete_calendar_link(task_id)


@router.callback_query(F.data.startswith("task:done:"), user_filter)
async def cb_task_done(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    await callback.message.edit_reply_markup(reply_markup=confirm_done_kb(key))
    await callback.answer("Подтвердить выполнение?")


@router.callback_query(F.data.startswith("task:done_yes:"), user_filter)
async def cb_task_done_yes(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    try:
        await ms_todo.complete_task(task_id)
        # Удаляем напоминание и событие календаря
        await _cleanup_task_extras(task_id)
        await callback.message.delete()
        header = await remove_task_header(key)
        if header:
            chat_id, header_message_id = header
            try:
                await callback.bot.delete_message(chat_id, header_message_id)
            except Exception:
                pass
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    await callback.answer("✅ Выполнено")


@router.callback_query(F.data.startswith("task:delete:"), user_filter)
async def cb_task_delete(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    await callback.message.edit_reply_markup(reply_markup=confirm_delete_kb(key))
    await callback.answer("Удалить задачу?")


@router.callback_query(F.data.startswith("task:delete_yes:"), user_filter)
async def cb_task_delete_yes(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    try:
        # Удаляем напоминание и событие календаря
        await _cleanup_task_extras(task_id)
        await ms_todo.delete_task(task_id)
        await callback.message.delete()
        header = await remove_task_header(key)
        if header:
            chat_id, header_message_id = header
            try:
                await callback.bot.delete_message(chat_id, header_message_id)
            except Exception:
                pass
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    await callback.answer("🗑 Удалено")


@router.callback_query(F.data.startswith("task:action_cancel:"), user_filter)
async def cb_task_action_cancel(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    await callback.message.edit_reply_markup(reply_markup=task_actions_kb(key))
    await callback.answer()


# --- Подробнее ---

@router.callback_query(F.data.startswith("task:more:"), user_filter)
async def cb_task_more(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    has_reminder = await storage.has_task_any_reminder(task_id)
    in_calendar = bool(await storage.get_calendar_link(task_id))
    await callback.message.edit_reply_markup(reply_markup=task_more_kb(key, has_reminder, in_calendar))
    await callback.answer()


@router.callback_query(F.data.startswith("task:back:"), user_filter)
async def cb_task_back(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    await callback.message.edit_reply_markup(reply_markup=task_actions_kb(key))
    await callback.answer()


# --- Редактирование из Подробнее ---

@router.callback_query(F.data.startswith("task:edit_title:"), user_filter)
async def cb_edit_title(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    prompt_msg = await callback.message.answer("✏️ Напиши новое название:")
    await storage.set_state(user_id, "editing_task_title", {
        "task_id": task_id,
        "key": key,
        "chat_id": callback.message.chat.id,
        "task_message_id": callback.message.message_id,
        "prompt_message_id": prompt_msg.message_id,
        "current_text": callback.message.text or "",
    })
    await callback.answer()


@router.callback_query(F.data.startswith("task:edit_date:"), user_filter)
async def cb_edit_date(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    prompt_msg = await callback.message.answer("📅 Напиши новую дату (например: завтра, в пятницу, 15 апреля):")
    await storage.set_state(user_id, "editing_task_date", {
        "task_id": task_id,
        "key": key,
        "chat_id": callback.message.chat.id,
        "task_message_id": callback.message.message_id,
        "prompt_message_id": prompt_msg.message_id,
        "current_text": callback.message.text or "",
    })
    await callback.answer()


# --- Напоминание из Подробнее ---

@router.callback_query(F.data.startswith("task:add_reminder:"), user_filter)
async def cb_task_add_reminder(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    try:
        task = await ms_todo.get_task(task_id)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return

    due_time = task.get("dueDateTime") and _extract_due_time(task)
    from services.ms_todo import _task_local_date
    iso_date = _task_local_date(task) or ""

    if due_time:
        prompt_text = "⏰ За сколько напомнить? (например: за 15 минут, за час, точно в 15:00)"
    else:
        prompt_text = "⏰ Когда напомнить? (например: сегодня в 13:00, завтра в 10:00, через 2 часа)"

    prompt_msg = await callback.bot.send_message(
        callback.message.chat.id, prompt_text,
    )
    await storage.set_state(user_id, "reminder_ask_offset", {
        "task_id": task_id,
        "task_title": task["title"],
        "due_date": iso_date,
        "due_time": due_time,
        "chat_id": callback.message.chat.id,
        "offset_q_msg_id": prompt_msg.message_id,
        "task_message_id": callback.message.message_id,
        "task_key": key,
    })

    await callback.answer()


@router.callback_query(F.data.startswith("task:del_reminder:"), user_filter)
async def cb_task_del_reminder(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    try:
        await storage.delete_telegram_reminder_by_task(task_id)
        await ms_todo.remove_reminder(task_id)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return

    await _update_task_message(callback.bot, callback.message.chat.id, callback.message.message_id, key, task_id)
    await callback.answer("⏰ Напоминание удалено")


@router.callback_query(F.data.startswith("task:edit_reminder:"), user_filter)
async def cb_edit_reminder(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    reminder = await storage.get_reminder_by_task(task_id)
    current_str = format_fire_at(reminder["fire_at"]) if reminder else "неизвестно"

    prompt_msg = await callback.bot.send_message(
        callback.message.chat.id,
        f"⏰ Текущее напоминание: {current_str}\nВведи новое время (например: завтра в 15:00, через 2 часа):",
    )
    await storage.set_state(user_id, "edit_reminder_waiting_input", {
        "task_id": task_id,
        "task_key": key,
        "chat_id": callback.message.chat.id,
        "task_message_id": callback.message.message_id,
        "prompt_msg_id": prompt_msg.message_id,
    })
    await callback.answer()


# --- Календарь из Подробнее ---

@router.callback_query(F.data.startswith("task:add_calendar:"), user_filter)
async def cb_task_add_calendar(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    try:
        task = await ms_todo.get_task(task_id)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return

    from services.ms_todo import _task_local_date
    iso_date = _task_local_date(task) or ""
    due_time = _extract_due_time(task)

    state_data = {
        "task_id": task_id,
        "task_title": task["title"],
        "due_date": iso_date,
        "due_time": due_time,
        "chat_id": callback.message.chat.id,
        "task_message_id": callback.message.message_id,
        "task_key": key,
    }

    if due_time:
        prompt_msg = await callback.bot.send_message(
            callback.message.chat.id,
            "🗓 Введи длительность события (например: 1 час, 30 минут):",
        )
        state_data["prompt_msg_id"] = prompt_msg.message_id
        await storage.set_state(user_id, "list_cal_waiting_duration", state_data)
    else:
        prompt_msg = await callback.bot.send_message(
            callback.message.chat.id,
            "🗓 Введи время и длительность (например: 15:00, 1 час):",
        )
        state_data["prompt_msg_id"] = prompt_msg.message_id
        await storage.set_state(user_id, "list_cal_waiting_time_duration", state_data)

    await callback.answer()


@router.callback_query(F.data.startswith("task:del_calendar:"), user_filter)
async def cb_task_del_calendar(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    event_id = await storage.delete_calendar_link(task_id)
    if event_id:
        try:
            await google_calendar.delete_event(event_id)
        except Exception:
            pass

    await _update_task_message(callback.bot, callback.message.chat.id, callback.message.message_id, key, task_id)
    await callback.answer("🗓 Убрано из календаря")


@router.callback_query(F.data.startswith("task:edit_calendar:"), user_filter)
async def cb_edit_calendar(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key

    event_id = await storage.get_calendar_link(task_id)
    if not event_id:
        await callback.answer("Событие не найдено", show_alert=True)
        return

    try:
        event = await google_calendar.get_event(event_id)
        current_str = format_event_info(event)
    except Exception:
        current_str = "неизвестно"
        event = {}

    # Вычисляем текущую длительность
    start_str = event.get("start", {}).get("dateTime", "")
    end_str = event.get("end", {}).get("dateTime", "")
    current_duration = 60
    current_event_time = None
    if start_str and end_str:
        from datetime import datetime
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)
        current_duration = int((end_dt - start_dt).total_seconds() / 60)
        local_start = start_dt.astimezone(ZoneInfo(config.USER_TIMEZONE))
        current_event_time = local_start.strftime("%H:%M")

    try:
        task = await ms_todo.get_task(task_id)
    except Exception:
        task = {}

    from services.ms_todo import _task_local_date
    iso_date = _task_local_date(task) or ""
    due_time = _extract_due_time(task)

    prompt_msg = await callback.bot.send_message(
        callback.message.chat.id,
        f"🗓 Текущее событие: {current_str}\nВведи новое время и/или длительность (например: 15:00, 2 часа):",
    )
    await storage.set_state(user_id, "edit_calendar_waiting_input", {
        "task_id": task_id,
        "task_key": key,
        "task_title": task.get("title", ""),
        "event_id": event_id,
        "due_date": iso_date,
        "due_time": due_time or current_event_time,
        "current_event_time": current_event_time,
        "current_duration_minutes": current_duration,
        "chat_id": callback.message.chat.id,
        "task_message_id": callback.message.message_id,
        "prompt_msg_id": prompt_msg.message_id,
    })
    await callback.answer()


# --- Напоминания (глобальный флоу) ---

@router.callback_query(F.data == "reminder:yes", user_filter)
async def cb_reminder_yes(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, state_data = await storage.get_state(user_id)
    if not state_data:
        await callback.answer()
        return

    offset_msg = await callback.bot.send_message(
        state_data["chat_id"],
        "⏰ За сколько напомнить? (например: за 15 минут, за час, точно в 15:00)",
    )
    await storage.set_state(user_id, "reminder_ask_offset", {
        "task_id": state_data["task_id"],
        "task_title": state_data["task_title"],
        "due_date": state_data["due_date"],
        "due_time": state_data["due_time"],
        "chat_id": state_data["chat_id"],
        "offset_q_msg_id": offset_msg.message_id,
    })
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "reminder:no", user_filter)
async def cb_reminder_no(callback: CallbackQuery):
    user_id = callback.from_user.id
    await storage.clear_state(user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "reminder:tg", user_filter)
async def cb_reminder_tg(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, state_data = await storage.get_state(user_id)
    if not state_data:
        await callback.answer()
        return

    try:
        await storage.save_reminder(
            state_data["chat_id"],
            state_data["fire_at_utc"],
            f"⏰ Напоминание: {state_data['task_title']}",
            task_id=state_data.get("task_id"),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
        await callback.answer()
        return

    # Обновляем маркеры на сообщении задачи если пришли из списка
    await _update_task_message_markers(callback, state_data)

    await storage.clear_state(user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("✅ Напоминание создано в Telegram")


@router.callback_query(F.data == "reminder:todo", user_filter)
async def cb_reminder_todo(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, state_data = await storage.get_state(user_id)
    if not state_data:
        await callback.answer()
        return

    try:
        await ms_todo.set_reminder(state_data["task_id"], state_data["fire_at_utc"])
        # Сохраняем в нашу БД для трекинга
        await storage.save_reminder(
            state_data["chat_id"],
            state_data["fire_at_utc"],
            f"⏰ Напоминание: {state_data['task_title']}",
            task_id=state_data.get("task_id"),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
        await callback.answer()
        return

    await _update_task_message_markers(callback, state_data)

    await storage.clear_state(user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("✅ Напоминание создано в MS Todo")


# --- Настройки ---

MODE_LABELS = {"all": "Все задачи", "uncertain": "Только неуверенные", "off": "Отключено"}


def settings_text(confirm_mode: str, research_enabled: bool) -> str:
    return (
        f"⚙️ Настройки\n\n"
        f"Режим подтверждения: {MODE_LABELS.get(confirm_mode, confirm_mode)}\n"
        f"Разведка в интернете (Perplexity): {'включена' if research_enabled else 'выключена'}\n\n"
        f"Разведка ищет готовые решения и технические ограничения по проекту, "
        f"примерно полцента за запуск.\n\n"
        f"Что меняем?"
    )


@router.callback_query(F.data.startswith("settings:confirm_mode:"), user_filter)
async def cb_settings_confirm_mode(callback: CallbackQuery):
    user_id = callback.from_user.id
    mode = callback.data.split(":", 2)[2]
    await storage.set_confirm_mode(user_id, mode)
    research_enabled = await storage.get_research_enabled(user_id)

    await callback.message.edit_text(
        settings_text(mode, research_enabled),
        reply_markup=settings_kb(mode, research_enabled),
    )
    await callback.answer(f"Сохранено: {MODE_LABELS.get(mode, mode)}")


@router.callback_query(F.data.startswith("settings:research:"), user_filter)
async def cb_settings_research(callback: CallbackQuery):
    user_id = callback.from_user.id
    enabled = callback.data.split(":", 2)[2] == "on"
    await storage.set_research_enabled(user_id, enabled)
    confirm_mode = await storage.get_confirm_mode(user_id)

    await callback.message.edit_text(
        settings_text(confirm_mode, enabled),
        reply_markup=settings_kb(confirm_mode, enabled),
    )
    await callback.answer("Разведка включена" if enabled else "Разведка выключена")


# --- Вспомогательные функции ---

def _extract_due_time(task: dict) -> str | None:
    """Извлекает время из dueDateTime задачи MS Todo."""
    due = task.get("dueDateTime")
    if not due:
        return None
    dt_str = due.get("dateTime", "")
    if not dt_str:
        return None
    tz_str = due.get("timeZone", "UTC")
    # Задачи без реального времени хранятся с timeZone=UTC (заглушка T20:00:00)
    if tz_str == "UTC":
        return None
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt_str_clean = dt_str.split(".")[0]
        tz = ZoneInfo(tz_str)
        dt = datetime.fromisoformat(dt_str_clean).replace(tzinfo=tz)
        local_dt = dt.astimezone(ZoneInfo(config.USER_TIMEZONE))
        time_str = local_dt.strftime("%H:%M")
        return time_str if time_str != "00:00" else None
    except Exception:
        return None


async def _update_task_message(bot, chat_id: int, message_id: int, task_key: str, task_id: str):
    """Обновляет текст и клавиатуру сообщения задачи (маркеры + детали)."""
    try:
        has_reminder = await storage.has_task_any_reminder(task_id)
        in_calendar = bool(await storage.get_calendar_link(task_id))

        # Получаем текущий текст и отделяем базовую часть (название + дата)
        # от деталей (напоминание, событие)
        # Т.к. нельзя получить текст через API — пересобираем из данных задачи
        task = await ms_todo.get_task(task_id)
        title = task.get("title", "")
        date_str = ms_todo.format_due_date_from_task(task)

        reminder_info = await storage.get_reminder_by_task(task_id) if has_reminder else None
        event_info = None
        if in_calendar:
            event_id = await storage.get_calendar_link(task_id)
            if event_id:
                try:
                    event_info = await google_calendar.get_event(event_id)
                except Exception:
                    pass

        from handlers.utils import build_scheduled_task_text, build_task_text
        if reminder_info or event_info:
            new_text = build_scheduled_task_text(
                title, date_str, has_reminder, in_calendar, reminder_info, event_info,
            )
        else:
            new_text = build_task_text(title, date_str, has_reminder, in_calendar)

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=new_text,
            reply_markup=task_actions_kb(task_key),
        )
    except Exception:
        pass


async def _update_task_message_markers(callback, state_data: dict):
    """Обновляет текст и клавиатуру сообщения задачи после изменения напоминания/календаря."""
    task_message_id = state_data.get("task_message_id")
    task_key = state_data.get("task_key")
    chat_id = state_data.get("chat_id")
    task_id = state_data.get("task_id")

    if not (task_message_id and task_key and chat_id and task_id):
        return

    await _update_task_message(callback.bot, chat_id, task_message_id, task_key, task_id)
