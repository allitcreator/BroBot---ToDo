from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def confirm_task_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Создать", callback_data="confirm:create"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data="confirm:edit"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:cancel"),
        ],
        [
            InlineKeyboardButton(text="🚀 Это проект", callback_data="confirm:project"),
        ],
    ])


def unclear_kb() -> InlineKeyboardMarkup:
    """Развилка, когда классификатор не уверен."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Задача", callback_data="unclear:task"),
        InlineKeyboardButton(text="🚀 Проект", callback_data="unclear:project"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="unclear:cancel"),
    ]])


def interview_session_kb() -> InlineKeyboardMarkup:
    """Кнопки под каждым вопросом интервью."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Достаточно", callback_data="interview:enough"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="interview:cancel"),
        ],
        [
            InlineKeyboardButton(text="🤖 Разбери сам", callback_data="interview:draft"),
            InlineKeyboardButton(text="📋 Это задача", callback_data="interview:to_task"),
        ],
    ])


def brief_review_kb() -> InlineKeyboardMarkup:
    """Показ собранного брифа перед записью в MS Todo."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💾 Сохранить проект", callback_data="brief:save"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="brief:cancel"),
    ]])


def project_actions_kb(key: str, research_enabled: bool = True) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="📖 Открыть", callback_data=f"proj:open:{key}"),
        InlineKeyboardButton(text="✏️ Дополнить", callback_data=f"proj:fill:{key}"),
        InlineKeyboardButton(text="🗑", callback_data=f"proj:delete:{key}"),
    ]]
    if research_enabled:
        rows.append([
            InlineKeyboardButton(text="🔎 Разведать", callback_data=f"proj:research:{key}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_fields_kb(key: str) -> InlineKeyboardMarkup:
    """Выбор секции брифа для дополнения."""
    from services.projects import SECTIONS
    rows, row = [], []
    for i, (_, title) in enumerate(SECTIONS):
        row.append(InlineKeyboardButton(text=title, callback_data=f"proj:field:{i}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"proj:back:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_confirm_delete_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить проект", callback_data=f"proj:delete_yes:{key}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"proj:back:{key}"),
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
        InlineKeyboardButton(text="⚙️ Подробнее", callback_data=f"task:more:{key}"),
    ]])


# Алиас для совместимости
def overdue_task_kb(key: str) -> InlineKeyboardMarkup:
    return task_actions_kb(key)


def task_more_kb(key: str, has_reminder: bool, in_calendar: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"task:edit_title:{key}"),
            InlineKeyboardButton(text="📅 Дата", callback_data=f"task:edit_date:{key}"),
        ],
    ]
    if has_reminder:
        rows.append([
            InlineKeyboardButton(text="⏰ Удалить", callback_data=f"task:del_reminder:{key}"),
            InlineKeyboardButton(text="⏰ Изменить", callback_data=f"task:edit_reminder:{key}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="⏰ Напомнить", callback_data=f"task:add_reminder:{key}"),
        ])
    if in_calendar:
        rows.append([
            InlineKeyboardButton(text="🗓 Убрать", callback_data=f"task:del_calendar:{key}"),
            InlineKeyboardButton(text="🗓 Изменить", callback_data=f"task:edit_calendar:{key}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="🗓 В календарь", callback_data=f"task:add_calendar:{key}"),
        ])
    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data=f"task:back:{key}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def reminder_ask_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data="reminder:yes"),
        InlineKeyboardButton(text="Нет", callback_data="reminder:no"),
    ]])


def reminder_where_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Telegram", callback_data="reminder:tg"),
        InlineKeyboardButton(text="MS Todo", callback_data="reminder:todo"),
    ]])


def settings_kb(confirm_mode: str, research_enabled: bool = True) -> InlineKeyboardMarkup:
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
    research_row = [InlineKeyboardButton(
        text=("✓ Разведка в интернете" if research_enabled else "Разведка выключена"),
        callback_data=f"settings:research:{'off' if research_enabled else 'on'}",
    )]
    return InlineKeyboardMarkup(inline_keyboard=[buttons, research_row])
