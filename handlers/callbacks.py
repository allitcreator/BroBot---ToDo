from aiogram import Router, F
from aiogram.types import CallbackQuery
import config
from db import storage
from db.storage import resolve_task_id, register_task_id, remove_task_header
from services import ms_todo, google_calendar
from handlers.keyboards import confirm_task_kb, calendar_ask_kb, settings_kb, confirm_done_kb, confirm_delete_kb, task_actions_kb, reminder_where_kb
from handlers.utils import ask_reminder_if_needed
from handlers.utils import format_task_preview

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
            subtasks=task.get("subtasks"),
            description=task.get("description"),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при создании задачи: {e}")
        await callback.answer()
        return

    await register_task_id(created["id"])
    await callback.message.answer(f"✅ Задача создана: {task['title']}")
    await callback.answer()

    # Спрашиваем про календарь только если это событие
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
        # Есть время и длительность — создаём сразу
        try:
            await google_calendar.create_event(
                title=state_data["title"],
                due_date=state_data["due_date"],
                due_time=state_data["due_time"],
                duration_minutes=state_data["duration_minutes"],
            )
            await callback.message.answer("✅ Событие добавлено в Google Calendar")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        await ask_reminder_if_needed(
            callback.bot, callback.message.chat.id, user_id,
            state_data["task_id"], state_data["title"], state_data["due_date"], state_data.get("due_time"),
        )

    elif state == "cal_waiting_duration":
        # Уже есть время, ждём длительность
        await callback.message.answer("Напиши длительность (например: 1 час, 30 минут):")

    elif state == "cal_waiting_time_duration":
        # Ждём время и длительность
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


# --- Действия с задачами ---

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


# --- Напоминания ---

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
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
        await callback.answer()
        return

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
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
        await callback.answer()
        return

    await storage.clear_state(user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("✅ Напоминание создано в MS Todo")


# --- Настройки ---

@router.callback_query(F.data.startswith("settings:confirm_mode:"), user_filter)
async def cb_settings_confirm_mode(callback: CallbackQuery):
    user_id = callback.from_user.id
    mode = callback.data.split(":", 2)[2]
    await storage.set_confirm_mode(user_id, mode)

    mode_labels = {"all": "Все задачи", "uncertain": "Только неуверенные", "off": "Отключено"}
    await callback.message.edit_text(
        f"⚙️ Настройки\n\n"
        f"Режим подтверждения: {mode_labels.get(mode, mode)}\n\n"
        f"Выбери режим:",
        reply_markup=settings_kb(mode),
    )
    await callback.answer(f"Сохранено: {mode_labels.get(mode, mode)}")
