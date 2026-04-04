from aiogram import Router, F, Bot
from aiogram.types import Message
import config
from db import storage
from services import llm, ms_todo, google_calendar
from handlers.keyboards import confirm_task_kb, calendar_ask_kb, reminder_where_kb
from handlers.utils import format_task_preview, ask_reminder_if_needed

router = Router()


def user_filter(message: Message) -> bool:
    return message.from_user.id == config.TELEGRAM_USER_ID


async def _extract_text(message: Message) -> str | None:
    """Извлекает текст из сообщения или транскрибирует голосовое."""
    if message.voice:
        await message.bot.send_chat_action(message.chat.id, "typing")
        file = await message.bot.get_file(message.voice.file_id)
        bio = await message.bot.download_file(file.file_path)
        text = await llm.transcribe_voice(bio.read())
        await message.reply(f"🎤 {text}")
        return text
    return message.text or message.caption or ""


@router.message(F.from_user.func(lambda u: u.id == config.TELEGRAM_USER_ID))
async def handle_message(message: Message):
    user_id = message.from_user.id

    # Проверяем текущее состояние
    state, state_data = await storage.get_state(user_id)

    if state == "editing_pending_title":
        await _handle_edit_pending_title(message, state_data)
        return

    if state == "editing_task_title":
        await _handle_edit_task_title(message, state_data)
        return

    if state == "editing_task_date":
        await _handle_edit_task_date(message, state_data)
        return

    if state == "reminder_ask_offset":
        await _handle_reminder_offset(message, state_data)
        return

    if state == "list_reminder_need_time":
        await _handle_list_reminder_need_time(message, state_data)
        return

    if state == "list_cal_waiting_duration":
        await _handle_list_cal_duration(message, state_data)
        return

    if state == "list_cal_waiting_time_duration":
        await _handle_list_cal_time_duration(message, state_data)
        return

    if state == "cal_waiting_time_duration":
        await _handle_cal_time_and_duration(message, state_data)
        return

    if state == "cal_waiting_duration":
        await _handle_cal_duration(message, state_data)
        return

    if state == "edit_reminder_waiting_input":
        await _handle_edit_reminder_input(message, state_data)
        return

    if state == "edit_calendar_waiting_input":
        await _handle_edit_calendar_input(message, state_data)
        return

    # Иначе — парсим как новую задачу
    await _handle_new_task(message)


def _get_forward_link(message: Message) -> str | None:
    """Пытается получить ссылку на оригинальное сообщение."""
    origin = message.forward_origin
    if not origin:
        return None
    if origin.type == "channel" and origin.chat:
        chat = origin.chat
        if chat.username:
            return f"https://t.me/{chat.username}/{origin.message_id}"
        else:
            chat_id = str(chat.id).replace("-100", "")
            return f"https://t.me/c/{chat_id}/{origin.message_id}"
    return None


def _build_forward_description(message: Message) -> str:
    """Собирает description из пересланного сообщения: текст + ссылка."""
    parts = []
    fwd_text = message.text or message.caption or ""
    if fwd_text:
        parts.append(fwd_text)
    fwd_link = _get_forward_link(message)
    if fwd_link:
        parts.append(fwd_link)
    return "\n\n".join(parts)


async def _handle_new_task(message: Message):
    user_id = message.from_user.id
    description = None

    # Пересланное сообщение — сохраняем отдельно
    if message.forward_origin:
        fwd_desc = _build_forward_description(message)
        await storage.save_forward(user_id, fwd_desc)

        # Если уже есть pending задача (комментарий обработался быстрее) — прикрепляем
        pending = await storage.get_pending_task(user_id)
        if pending:
            pending["description"] = fwd_desc
            await storage.save_pending_task(user_id, pending)
            preview = format_task_preview(pending)
            await message.answer(
                f"📋 Создать задачу?\n\n{preview}",
                reply_markup=confirm_task_kb(),
            )
            return

        # Ждём 2 секунды — если комментарий не придёт, спросим
        import asyncio
        await asyncio.sleep(2)
        # Проверяем: если форвард всё ещё в БД (не подхвачен комментарием) — спрашиваем
        still_saved = await storage.pop_forward(user_id)
        if still_saved:
            await storage.save_forward(user_id, still_saved)
            await message.answer("💬 Напиши что сделать с этим сообщением (или /skip чтобы пропустить):")
        return

    try:
        text = await _extract_text(message)
    except Exception as e:
        await message.answer(f"❌ Не удалось распознать голосовое: {e}")
        return

    if not text:
        return

    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        task = await llm.parse_task(text)
    except Exception as e:
        await message.answer(f"❌ Ошибка при разборе задачи: {e}")
        return

    # Подхватываем сохранённый форвард (мог прийти до или во время LLM вызова)
    saved_fwd = await storage.pop_forward(user_id)
    if saved_fwd:
        task["description"] = saved_fwd

    confirm_mode = await storage.get_confirm_mode(user_id)
    need_confirm = (
        confirm_mode == "all"
        or (confirm_mode == "uncertain" and task.get("confidence") == "low")
    )

    if need_confirm:
        await storage.save_pending_task(user_id, task)
        preview = format_task_preview(task)
        await message.answer(
            f"📋 Создать задачу?\n\n{preview}",
            reply_markup=confirm_task_kb(),
        )
    else:
        await _create_task_and_ask_calendar(message, task)


async def _create_task_and_ask_calendar(message: Message, task: dict):
    user_id = message.from_user.id
    try:
        created = await ms_todo.create_task(
            title=task["title"],
            due_date=task["due_date"],
            due_time=task.get("due_time"),
            subtasks=task.get("subtasks"),
            description=task.get("description"),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при создании задачи: {e}")
        return

    await message.answer(f"✅ Задача создана: {task['title']}")

    # Спрашиваем про календарь только если это событие
    if not task.get("is_event"):
        await ask_reminder_if_needed(
            message.bot, message.chat.id, user_id,
            created["id"], task["title"], task["due_date"], task.get("due_time"),
        )
        return

    # Сохраняем данные для возможного добавления в календарь
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
        await message.answer(
            "📅 Добавить событие в Google Calendar?",
            reply_markup=calendar_ask_kb(),
        )
    elif has_time:
        await storage.set_state(user_id, "cal_waiting_duration", cal_data)
        await message.answer(
            "📅 Добавить в Google Calendar?\n"
            "Если да — напиши длительность (например: 1 час, 30 минут).\n"
            "Или нажми /skip чтобы пропустить.",
            reply_markup=calendar_ask_kb(),
        )
    else:
        await storage.set_state(user_id, "cal_waiting_time_duration", cal_data)
        await message.answer(
            "📅 Добавить в Google Calendar?\n"
            "Если да — напиши время и длительность (например: в 14:00, 1 час).\n"
            "Или нажми /skip чтобы пропустить.",
            reply_markup=calendar_ask_kb(),
        )


async def _handle_forward_comment(message: Message, state_data: dict):
    user_id = message.from_user.id
    await storage.clear_state(user_id)

    text = message.text or ""
    if not text:
        await message.answer("❌ Нужен текст. Попробуй ещё раз или /skip")
        return

    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        task = await llm.parse_task(text)
    except Exception as e:
        await message.answer(f"❌ Ошибка при разборе задачи: {e}")
        return

    task["description"] = state_data.get("description", "")

    confirm_mode = await storage.get_confirm_mode(user_id)
    need_confirm = (
        confirm_mode == "all"
        or (confirm_mode == "uncertain" and task.get("confidence") == "low")
    )

    if need_confirm:
        await storage.save_pending_task(user_id, task)
        preview = format_task_preview(task)
        await message.answer(
            f"📋 Создать задачу?\n\n{preview}",
            reply_markup=confirm_task_kb(),
        )
    else:
        await _create_task_and_ask_calendar(message, task)


async def _handle_edit_pending_title(message: Message, state_data: dict):
    user_id = message.from_user.id
    task = await storage.get_pending_task(user_id)
    if not task:
        await storage.clear_state(user_id)
        return

    text = await _extract_text(message)
    if not text:
        return
    task["title"] = text
    await storage.save_pending_task(user_id, task)
    await storage.clear_state(user_id)

    preview = format_task_preview(task)
    await message.answer(
        f"📋 Создать задачу?\n\n{preview}",
        reply_markup=confirm_task_kb(),
    )


async def _handle_edit_task_title(message: Message, state_data: dict):
    user_id = message.from_user.id
    task_id = state_data.get("task_id")
    key = state_data.get("key")
    chat_id = state_data.get("chat_id")
    task_message_id = state_data.get("task_message_id")
    prompt_message_id = state_data.get("prompt_message_id")
    current_text = state_data.get("current_text", "")

    text = await _extract_text(message)
    if not text:
        return

    try:
        await ms_todo.update_task(task_id, title=text)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    # Обновляем исходное сообщение задачи
    if task_message_id and chat_id and key:
        if " — " in current_text:
            date_part = current_text[current_text.find(" — "):]
            new_text = text + date_part
        else:
            new_text = text
        try:
            from handlers.keyboards import task_actions_kb
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=task_message_id,
                text=new_text,
                reply_markup=task_actions_kb(key),
            )
        except Exception:
            pass

    # Удаляем сервисные сообщения
    if prompt_message_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_message_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    await storage.clear_state(user_id)


async def _handle_edit_task_date(message: Message, state_data: dict):
    user_id = message.from_user.id
    task_id = state_data.get("task_id")
    key = state_data.get("key")
    chat_id = state_data.get("chat_id")
    task_message_id = state_data.get("task_message_id")
    prompt_message_id = state_data.get("prompt_message_id")
    current_text = state_data.get("current_text", "")

    text = await _extract_text(message)
    if not text:
        return

    try:
        parsed = await llm.parse_task(text)
        new_date = parsed.get("due_date")
        if not new_date:
            await message.answer("❌ Не удалось распознать дату")
            return
        await ms_todo.update_task(task_id, due_date=new_date)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    # Обновляем исходное сообщение задачи
    if task_message_id and chat_id and key:
        from datetime import date
        d = date.fromisoformat(new_date)
        new_date_str = d.strftime("%d.%m.%Y")
        if " — " in current_text:
            title_part = current_text[:current_text.find(" — ")]
        else:
            title_part = current_text
        new_text = f"{title_part} — {new_date_str}"
        try:
            from handlers.keyboards import task_actions_kb
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=task_message_id,
                text=new_text,
                reply_markup=task_actions_kb(key),
            )
        except Exception:
            pass

    # Удаляем сервисные сообщения
    if prompt_message_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_message_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    await storage.clear_state(user_id)


async def _handle_cal_time_and_duration(message: Message, state_data: dict):
    user_id = message.from_user.id
    text = await _extract_text(message)
    if not text:
        return
    try:
        details = await llm.parse_calendar_details(text)
        due_time = details.get("due_time") or state_data.get("due_time")
        duration_minutes = details.get("duration_minutes") or state_data.get("duration_minutes")

        if not due_time and not duration_minutes:
            await message.answer("❌ Не удалось распознать. Попробуй ещё раз или /skip")
            return

        if due_time and not duration_minutes:
            state_data["due_time"] = due_time
            await storage.set_state(user_id, "cal_waiting_duration", state_data)
            await message.answer("⏱ Теперь напиши длительность (например: 1 час, 30 минут):")
            return

        if duration_minutes and not due_time:
            state_data["duration_minutes"] = duration_minutes
            await storage.set_state(user_id, "cal_waiting_time_duration", state_data)
            await message.answer("🕐 Теперь напиши время начала (например: 15:00):")
            return

        event = await google_calendar.create_event(
            title=state_data["title"],
            due_date=state_data["due_date"],
            due_time=due_time,
            duration_minutes=duration_minutes,
        )
        if state_data.get("task_id"):
            await storage.save_calendar_link(state_data["task_id"], event["id"])
        await message.answer("✅ Событие добавлено в Google Calendar")
        await storage.clear_state(user_id)
        await ask_reminder_if_needed(
            message.bot, message.chat.id, user_id,
            state_data.get("task_id", ""), state_data["title"], state_data["due_date"], state_data.get("due_time"),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении в календарь: {e}")
        await storage.clear_state(user_id)


async def _handle_reminder_offset(message: Message, state_data: dict):
    user_id = message.from_user.id
    chat_id = state_data.get("chat_id")
    offset_q_msg_id = state_data.get("offset_q_msg_id")

    text = await _extract_text(message)
    if not text:
        return

    try:
        fire_at_utc = await llm.parse_reminder_offset(
            text, state_data["due_date"], state_data["due_time"]
        )
        if not fire_at_utc:
            await message.answer("❌ Не удалось распознать время. Попробуй ещё раз (например: за 15 минут, за час, точно в 15:00):")
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    # Удаляем сервисные сообщения
    if offset_q_msg_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, offset_q_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    where_msg = await message.bot.send_message(
        chat_id,
        "Где создать напоминание?",
        reply_markup=reminder_where_kb(),
    )
    await storage.set_state(user_id, "reminder_choosing_where", {
        "task_id": state_data["task_id"],
        "task_title": state_data["task_title"],
        "fire_at_utc": fire_at_utc,
        "chat_id": chat_id,
        "where_q_msg_id": where_msg.message_id,
        "task_message_id": state_data.get("task_message_id"),
        "task_key": state_data.get("task_key"),
    })


async def _handle_cal_duration(message: Message, state_data: dict):
    user_id = message.from_user.id
    text = await _extract_text(message)
    if not text:
        return
    try:
        details = await llm.parse_calendar_details(text)
        duration_minutes = details.get("duration_minutes")

        if not duration_minutes:
            await message.answer("❌ Не удалось распознать длительность. Попробуй ещё раз или /skip")
            return

        event = await google_calendar.create_event(
            title=state_data["title"],
            due_date=state_data["due_date"],
            due_time=state_data["due_time"],
            duration_minutes=duration_minutes,
        )
        if state_data.get("task_id"):
            await storage.save_calendar_link(state_data["task_id"], event["id"])
        await message.answer("✅ Событие добавлено в Google Calendar")
        await storage.clear_state(user_id)
        await ask_reminder_if_needed(
            message.bot, message.chat.id, user_id,
            state_data.get("task_id", ""), state_data["title"], state_data["due_date"], state_data.get("due_time"),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении в календарь: {e}")
        await storage.clear_state(user_id)


async def _handle_list_reminder_need_time(message: Message, state_data: dict):
    """Пользователь ввёл время для задачи без due_time перед созданием напоминания."""
    user_id = message.from_user.id
    chat_id = state_data.get("chat_id")
    prompt_msg_id = state_data.get("prompt_msg_id")

    text = await _extract_text(message)
    if not text:
        return

    try:
        details = await llm.parse_calendar_details(text)
        due_time = details.get("due_time")
        if not due_time:
            await message.answer("❌ Не удалось распознать время. Попробуй ещё раз (например: 15:00):")
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    if prompt_msg_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    offset_msg = await message.bot.send_message(
        chat_id,
        "⏰ За сколько напомнить? (например: за 15 минут, за час, точно в 15:00)",
    )
    await storage.set_state(user_id, "reminder_ask_offset", {
        "task_id": state_data["task_id"],
        "task_title": state_data["task_title"],
        "due_date": state_data["due_date"],
        "due_time": due_time,
        "chat_id": chat_id,
        "offset_q_msg_id": offset_msg.message_id,
        "task_message_id": state_data.get("task_message_id"),
        "task_key": state_data.get("task_key"),
    })


async def _handle_list_cal_duration(message: Message, state_data: dict):
    """Пользователь ввёл длительность события при добавлении в календарь из списка."""
    user_id = message.from_user.id
    chat_id = state_data.get("chat_id")
    prompt_msg_id = state_data.get("prompt_msg_id")

    text = await _extract_text(message)
    if not text:
        return

    try:
        details = await llm.parse_calendar_details(text)
        duration_minutes = details.get("duration_minutes")
        if not duration_minutes:
            await message.answer("❌ Не удалось распознать длительность. Попробуй ещё раз (например: 1 час, 30 минут):")
            return

        event = await google_calendar.create_event(
            title=state_data["task_title"],
            due_date=state_data["due_date"],
            due_time=state_data["due_time"],
            duration_minutes=duration_minutes,
        )
        await storage.save_calendar_link(state_data["task_id"], event["id"])
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    if prompt_msg_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    task_message_id = state_data.get("task_message_id")
    task_key = state_data.get("task_key")
    if task_message_id and task_key and chat_id:
        try:
            from handlers.keyboards import task_actions_kb
            await message.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=task_message_id, reply_markup=task_actions_kb(task_key),
            )
        except Exception:
            pass

    await storage.clear_state(user_id)
    await message.bot.send_message(chat_id, "✅ Событие добавлено в Google Calendar")


async def _handle_list_cal_time_duration(message: Message, state_data: dict):
    """Пользователь ввёл время и длительность при добавлении в календарь из списка."""
    user_id = message.from_user.id
    chat_id = state_data.get("chat_id")
    prompt_msg_id = state_data.get("prompt_msg_id")

    text = await _extract_text(message)
    if not text:
        return

    try:
        details = await llm.parse_calendar_details(text)
        due_time = details.get("due_time") or state_data.get("due_time")
        duration_minutes = details.get("duration_minutes") or state_data.get("duration_minutes")

        if not due_time and not duration_minutes:
            await message.answer("❌ Не удалось распознать. Попробуй (например: 15:00, 1 час):")
            return

        if due_time and not duration_minutes:
            state_data["due_time"] = due_time
            await storage.set_state(user_id, "list_cal_waiting_duration", state_data)
            await message.answer("⏱ Теперь напиши длительность (например: 1 час, 30 минут):")
            return

        if duration_minutes and not due_time:
            state_data["duration_minutes"] = duration_minutes
            await storage.set_state(user_id, "list_cal_waiting_time_duration", state_data)
            await message.answer("🕐 Теперь напиши время начала (например: 15:00):")
            return

        event = await google_calendar.create_event(
            title=state_data["task_title"],
            due_date=state_data["due_date"],
            due_time=due_time,
            duration_minutes=duration_minutes,
        )
        await storage.save_calendar_link(state_data["task_id"], event["id"])
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    if prompt_msg_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    task_message_id = state_data.get("task_message_id")
    task_key = state_data.get("task_key")
    if task_message_id and task_key and chat_id:
        try:
            from handlers.keyboards import task_actions_kb
            await message.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=task_message_id, reply_markup=task_actions_kb(task_key),
            )
        except Exception:
            pass

    await storage.clear_state(user_id)
    await message.bot.send_message(chat_id, "✅ Событие добавлено в Google Calendar")


async def _handle_edit_reminder_input(message: Message, state_data: dict):
    """Пользователь ввёл новое время напоминания."""
    user_id = message.from_user.id
    chat_id = state_data.get("chat_id")
    prompt_msg_id = state_data.get("prompt_msg_id")
    task_id = state_data["task_id"]

    text = await _extract_text(message)
    if not text:
        return

    try:
        task = await ms_todo.get_task(task_id)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    from handlers.callbacks import _extract_due_time
    from services.ms_todo import _task_local_date
    due_date = _task_local_date(task) or ""
    due_time = _extract_due_time(task) or ""

    if not due_time:
        await message.answer("❌ У задачи нет времени, невозможно рассчитать напоминание.")
        await storage.clear_state(user_id)
        return

    try:
        fire_at_utc = await llm.parse_reminder_offset(text, due_date, due_time)
        if not fire_at_utc:
            await message.answer("❌ Не удалось распознать время. Попробуй (например: за 15 минут, завтра в 10:00):")
            return
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    await storage.delete_telegram_reminder_by_task(task_id)
    try:
        await ms_todo.remove_reminder(task_id)
    except Exception:
        pass

    if prompt_msg_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    where_msg = await message.bot.send_message(
        chat_id, "Где создать напоминание?", reply_markup=reminder_where_kb(),
    )
    await storage.set_state(user_id, "reminder_choosing_where", {
        "task_id": task_id,
        "task_title": task.get("title", ""),
        "fire_at_utc": fire_at_utc,
        "chat_id": chat_id,
        "where_q_msg_id": where_msg.message_id,
        "task_message_id": state_data.get("task_message_id"),
        "task_key": state_data.get("task_key"),
    })


async def _handle_edit_calendar_input(message: Message, state_data: dict):
    """Пользователь ввёл новое время/длительность события."""
    user_id = message.from_user.id
    chat_id = state_data.get("chat_id")
    prompt_msg_id = state_data.get("prompt_msg_id")

    text = await _extract_text(message)
    if not text:
        return

    try:
        details = await llm.parse_calendar_details(text)
        new_time = details.get("due_time") or state_data.get("current_event_time") or state_data.get("due_time")
        new_duration = details.get("duration_minutes") or state_data.get("current_duration_minutes")

        if not new_time:
            await message.answer("❌ Не удалось распознать. Попробуй (например: 15:00, 2 часа):")
            return

        await google_calendar.delete_event(state_data["event_id"])
        event = await google_calendar.create_event(
            title=state_data["task_title"],
            due_date=state_data["due_date"],
            due_time=new_time,
            duration_minutes=new_duration,
        )
        await storage.save_calendar_link(state_data["task_id"], event["id"])
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await storage.clear_state(user_id)
        return

    if prompt_msg_id and chat_id:
        try:
            await message.bot.delete_message(chat_id, prompt_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    task_message_id = state_data.get("task_message_id")
    task_key = state_data.get("task_key")
    if task_message_id and task_key and chat_id:
        try:
            from handlers.keyboards import task_actions_kb
            await message.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=task_message_id, reply_markup=task_actions_kb(task_key),
            )
        except Exception:
            pass

    await storage.clear_state(user_id)
    await message.bot.send_message(chat_id, "✅ Событие обновлено в Google Calendar")
