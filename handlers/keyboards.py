from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def confirm_task_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Создать", callback_data="confirm:create"),
        InlineKeyboardButton(text="✏️ Изменить", callback_data="confirm:edit"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:cancel"),
    ]])


def calendar_ask_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Добавить в календарь", callback_data="cal:yes"),
        InlineKeyboardButton(text="Пропустить", callback_data="cal:no"),
    ]])


def task_actions_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅", callback_data=f"task:done:{key}"),
        InlineKeyboardButton(text="❌", callback_data=f"task:delete:{key}"),
        InlineKeyboardButton(text="✏️ Название", callback_data=f"task:edit_title:{key}"),
        InlineKeyboardButton(text="📅 Дата", callback_data=f"task:edit_date:{key}"),
    ]])


def overdue_task_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅", callback_data=f"task:done:{key}"),
        InlineKeyboardButton(text="❌", callback_data=f"task:delete:{key}"),
        InlineKeyboardButton(text="✏️ Название", callback_data=f"task:edit_title:{key}"),
        InlineKeyboardButton(text="📅 Дата", callback_data=f"task:edit_date:{key}"),
    ]])


def confirm_done_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"task:done_yes:{key}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"task:action_cancel:{key}"),
    ]])


def confirm_delete_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"task:delete_yes:{key}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"task:action_cancel:{key}"),
    ]])


def settings_kb(confirm_mode: str) -> InlineKeyboardMarkup:
    modes = [
        ("all", "Все задачи"),
        ("uncertain", "Только неуверенные"),
        ("off", "Отключить"),
    ]
    buttons = []
    for mode, label in modes:
        mark = "✓ " if mode == confirm_mode else ""
        buttons.append(InlineKeyboardButton(
            text=f"{mark}{label}",
            callback_data=f"settings:confirm_mode:{mode}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])
