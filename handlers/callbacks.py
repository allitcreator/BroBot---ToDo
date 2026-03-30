from aiogram import Router, F
from aiogram.types import CallbackQuery
import config
from db import storage
from db.storage import resolve_task_id, register_task_id
from services import ms_todo, google_calendar
from handlers.keyboards import confirm_task_kb, calendar_ask_kb, settings_kb
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

    elif state == "cal_waiting_duration":
        # Уже есть время, ждём длительность
        await callback.message.answer("Напиши длительность (например: 1 час, 30 минут):")

    elif state == "cal_waiting_time_duration":
        # Ждём время и длительность
        await callback.message.answer("Напиши время и длительность (например: в 14:00, 1 час):")


@router.callback_query(F.data == "cal:no", user_filter)
async def cb_cal_no(callback: CallbackQuery):
    user_id = callback.from_user.id
    await storage.clear_state(user_id)
    await callback.message.edit_reply_markup()
    await callback.answer("Пропущено")


# --- Действия с задачами ---

@router.callback_query(F.data.startswith("task:done:"), user_filter)
async def cb_task_done(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    try:
        await ms_todo.complete_task(task_id)
        await callback.message.edit_reply_markup()
        await callback.message.answer("✅ Задача выполнена!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("task:edit_title:"), user_filter)
async def cb_edit_title(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    await storage.set_state(user_id, "editing_task_title", {"task_id": task_id})
    await callback.message.answer("✏️ Напиши новое название:")
    await callback.answer()


@router.callback_query(F.data.startswith("task:edit_date:"), user_filter)
async def cb_edit_date(callback: CallbackQuery):
    user_id = callback.from_user.id
    key = callback.data.split(":", 2)[2]
    task_id = await resolve_task_id(key) or key
    await storage.set_state(user_id, "editing_task_date", {"task_id": task_id})
    await callback.message.answer("📅 Напиши новую дату (например: завтра, в пятницу, 15 апреля):")
    await callback.answer()


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
