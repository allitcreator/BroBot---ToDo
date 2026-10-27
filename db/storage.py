import json
import hashlib
import aiosqlite
import config

_db: aiosqlite.Connection | None = None


async def init_db():
    global _db
    import os
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    _db = await aiosqlite.connect(config.DB_PATH)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            confirm_mode TEXT NOT NULL DEFAULT 'all'
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS pending_tasks (
            user_id INTEGER PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            data TEXT
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS task_ids (
            key TEXT PRIMARY KEY,
            full_id TEXT NOT NULL UNIQUE
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS saved_forwards (
            user_id INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS task_headers (
            key TEXT PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            header_message_id INTEGER NOT NULL
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            fire_at TEXT NOT NULL,
            text TEXT NOT NULL,
            task_id TEXT
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS task_calendar_links (
            task_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS ritual_runs (
            kind TEXT NOT NULL,
            day TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            snooze_at TEXT,
            snoozed INTEGER NOT NULL DEFAULT 0,
            message_id INTEGER,
            done_at TEXT,
            PRIMARY KEY (kind, day)
        )
    """)
    await _db.commit()

    # Migrate: add task_id column to reminders if it doesn't exist yet
    try:
        await _db.execute("ALTER TABLE reminders ADD COLUMN task_id TEXT")
        await _db.commit()
    except Exception:
        pass  # column already exists

    # Migrate: поля доставки напоминаний. Глотаем только «колонка уже есть».
    for column in ("attempts INTEGER NOT NULL DEFAULT 0", "source TEXT", "sent_at TEXT"):
        try:
            await _db.execute(f"ALTER TABLE reminders ADD COLUMN {column}")
            await _db.commit()
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
    # Что уже доставлено или брошено: синк не заводит такую пару заново,
    # пока MS Todo хранит прошедший reminderDateTime.
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            markdown TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            read_at TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT NOT NULL UNIQUE
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS reminder_log (
            task_id TEXT NOT NULL,
            fire_at TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'sent',
            PRIMARY KEY (task_id, fire_at)
        )
    """)
    await _db.commit()
    # kind: 'sent' — доставлено (повторно не слать); 'suppress' — запись убрали
    # или перенесли вручную (синк не должен воскрешать её по устаревшему кэшу).
    try:
        await _db.execute("ALTER TABLE reminder_log ADD COLUMN kind TEXT NOT NULL DEFAULT 'sent'")
        await _db.commit()
    except aiosqlite.OperationalError as e:
        if "duplicate column" not in str(e):
            raise

    # Migrate: разведка через Perplexity, включена по умолчанию
    try:
        await _db.execute("ALTER TABLE settings ADD COLUMN research_enabled INTEGER NOT NULL DEFAULT 1")
        await _db.commit()
    except Exception:
        pass  # column already exists

    # Migrate: разбор идей в проекты, включён по умолчанию
    try:
        await _db.execute("ALTER TABLE settings ADD COLUMN projects_enabled INTEGER NOT NULL DEFAULT 1")
        await _db.commit()
    except Exception:
        pass  # column already exists

    # Migrate: настройки утреннего и вечернего ритуалов
    for column, sql_type, default in _RITUAL_COLUMNS:
        try:
            await _db.execute(f"ALTER TABLE settings ADD COLUMN {column} {sql_type} NOT NULL DEFAULT {default!r}")
            await _db.commit()
        except Exception:
            pass  # column already exists


# Ритуалы разбора: колонка, тип, значение по умолчанию. Булевы — 0/1.
_RITUAL_COLUMNS = (
    ("morning_enabled", "INTEGER", 1),
    ("morning_time", "TEXT", "09:30"),
    ("evening_enabled", "INTEGER", 1),
    ("evening_time", "TEXT", "20:30"),
    ("stuck_days", "INTEGER", 14),
    ("weekend_rituals", "INTEGER", 0),
)
_RITUAL_BOOLS = {"morning_enabled", "evening_enabled", "weekend_rituals"}


async def close_db():
    if _db:
        await _db.close()


# --- Settings ---

async def get_confirm_mode(user_id: int) -> str:
    async with _db.execute(
        "SELECT confirm_mode FROM settings WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else "all"


async def get_research_enabled(user_id: int) -> bool:
    """Разрешён ли поиск в интернете через Perplexity. По умолчанию да."""
    async with _db.execute(
        "SELECT research_enabled FROM settings WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row[0]) if row and row[0] is not None else True


async def set_research_enabled(user_id: int, enabled: bool):
    await _db.execute(
        "INSERT INTO settings (user_id, research_enabled) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET research_enabled = excluded.research_enabled",
        (user_id, int(enabled)),
    )
    await _db.commit()


async def get_projects_enabled(user_id: int) -> bool:
    """Разбирает ли бот идеи в проекты через интервью. По умолчанию да."""
    async with _db.execute(
        "SELECT projects_enabled FROM settings WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row[0]) if row and row[0] is not None else True


async def set_projects_enabled(user_id: int, enabled: bool):
    await _db.execute(
        "INSERT INTO settings (user_id, projects_enabled) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET projects_enabled = excluded.projects_enabled",
        (user_id, int(enabled)),
    )
    await _db.commit()


async def set_confirm_mode(user_id: int, mode: str):
    await _db.execute(
        "INSERT INTO settings (user_id, confirm_mode) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET confirm_mode = excluded.confirm_mode",
        (user_id, mode),
    )
    await _db.commit()


async def get_ritual_settings(user_id: int) -> dict:
    """Настройки ритуалов; строки ещё нет — значения по умолчанию."""
    columns = [c for c, _, _ in _RITUAL_COLUMNS]
    async with _db.execute(
        f"SELECT {', '.join(columns)} FROM settings WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    values = row or [d for _, _, d in _RITUAL_COLUMNS]
    return {c: bool(v) if c in _RITUAL_BOOLS else v for c, v in zip(columns, values)}


async def set_ritual_settings(user_id: int, changes: dict):
    """Меняет только переданные поля; имена — из _RITUAL_COLUMNS."""
    allowed = {c for c, _, _ in _RITUAL_COLUMNS}
    if not changes or set(changes) - allowed:
        raise ValueError(f"unknown ritual settings: {set(changes) - allowed}")
    columns = list(changes)
    values = [int(changes[c]) if c in _RITUAL_BOOLS else changes[c] for c in columns]
    await _db.execute(
        f"INSERT INTO settings (user_id, {', '.join(columns)}) VALUES (?, {', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(user_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in columns)}",
        (user_id, *values),
    )
    await _db.commit()


# --- Pending tasks (awaiting confirmation) ---

async def save_pending_task(user_id: int, task: dict):
    await _db.execute(
        "INSERT INTO pending_tasks (user_id, data) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
        (user_id, json.dumps(task)),
    )
    await _db.commit()


async def get_pending_task(user_id: int) -> dict | None:
    async with _db.execute(
        "SELECT data FROM pending_tasks WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return json.loads(row[0]) if row else None


async def delete_pending_task(user_id: int):
    await _db.execute("DELETE FROM pending_tasks WHERE user_id = ?", (user_id,))
    await _db.commit()


# --- User states (FSM) ---

async def set_state(user_id: int, state: str, data: dict | None = None):
    await _db.execute(
        "INSERT INTO user_states (user_id, state, data) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, data = excluded.data",
        (user_id, state, json.dumps(data) if data else None),
    )
    await _db.commit()


async def get_state(user_id: int) -> tuple[str | None, dict | None]:
    async with _db.execute(
        "SELECT state, data FROM user_states WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None, None
    return row[0], json.loads(row[1]) if row[1] else None


async def clear_state(user_id: int):
    await _db.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    await _db.commit()


# --- Task ID registry (persistent) ---

def _make_key(full_id: str) -> str:
    return hashlib.sha1(full_id.encode()).hexdigest()[:8]


async def register_task_id(full_id: str) -> str:
    key = _make_key(full_id)
    await _db.execute(
        "INSERT OR IGNORE INTO task_ids (key, full_id) VALUES (?, ?)",
        (key, full_id),
    )
    await _db.commit()
    return key


async def resolve_task_id(key: str) -> str | None:
    async with _db.execute(
        "SELECT full_id FROM task_ids WHERE key = ?", (key,)
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


# --- Saved forwards ---

async def save_forward(user_id: int, description: str):
    await _db.execute(
        "INSERT INTO saved_forwards (user_id, description) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET description = excluded.description",
        (user_id, description),
    )
    await _db.commit()


async def pop_forward(user_id: int) -> str | None:
    async with _db.execute(
        "SELECT description FROM saved_forwards WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row:
        await _db.execute("DELETE FROM saved_forwards WHERE user_id = ?", (user_id,))
        await _db.commit()
        return row[0]
    return None


# --- Reminders ---

def _norm_fire_at(fire_at_utc: str) -> str:
    return fire_at_utc.replace("+00:00", "").split(".")[0]


async def save_reminder(chat_id: int, fire_at_utc: str, text: str, task_id: str | None = None,
                        source: str | None = None) -> int:
    """fire_at_utc — ISO datetime без timezone, всегда UTC.

    source='sync' — запись заведена синком из MS Todo; только такие синк
    имеет право снимать, когда напоминание пропало в MS Todo.
    """
    await _unsuppress(task_id, _norm_fire_at(fire_at_utc))
    cursor = await _db.execute(
        "INSERT INTO reminders (chat_id, fire_at, text, task_id, source) VALUES (?, ?, ?, ?, ?)",
        (chat_id, _norm_fire_at(fire_at_utc), text, task_id, source),
    )
    await _db.commit()
    return cursor.lastrowid


async def get_due_reminders() -> list[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    async with _db.execute(
        "SELECT id, chat_id, text, task_id, attempts, fire_at FROM reminders "
        "WHERE fire_at <= ? AND sent_at IS NULL", (now,)
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"id": r[0], "chat_id": r[1], "text": r[2], "task_id": r[3], "attempts": r[4], "fire_at": r[5]} for r in rows]


async def _log_reminders(where: str, params: tuple, kind: str = "suppress"):
    """Пишет пары (task_id, fire_at) выбранных записей в журнал.

    'sent' перекрывает 'suppress', обратное — нет.
    """
    await _db.execute(
        "INSERT INTO reminder_log (task_id, fire_at, logged_at, kind) "
        "SELECT task_id, fire_at, datetime('now'), ? FROM reminders WHERE task_id IS NOT NULL AND "
        + where +
        " ON CONFLICT(task_id, fire_at) DO UPDATE SET kind = CASE WHEN excluded.kind = 'sent' "
        "THEN 'sent' ELSE reminder_log.kind END",
        (kind, *params),
    )


async def _log_reminder(reminder_id: int, kind: str = "suppress"):
    await _log_reminders("id = ?", (reminder_id,), kind)


async def _unsuppress(task_id: str | None, fire_at: str):
    """Пара снова нужна (запись создана или перенесена на неё): снимаем подавление."""
    if task_id:
        await _db.execute(
            "DELETE FROM reminder_log WHERE task_id = ? AND fire_at = ? AND kind = 'suppress'", (task_id, fire_at)
        )


async def mark_reminder_sent(reminder_id: int):
    """Отправлено: запись больше не due, даже если удалить её не удастся."""
    await _log_reminder(reminder_id, "sent")
    await _db.execute("UPDATE reminders SET sent_at = datetime('now') WHERE id = ?", (reminder_id,))
    await _db.commit()


async def delete_sent_reminders():
    await _db.execute("DELETE FROM reminders WHERE sent_at IS NOT NULL")
    await _db.commit()


async def bump_reminder_attempts(reminder_id: int) -> int:
    await _db.execute("UPDATE reminders SET attempts = attempts + 1 WHERE id = ?", (reminder_id,))
    await _db.commit()
    async with _db.execute("SELECT attempts FROM reminders WHERE id = ?", (reminder_id,)) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else 0


async def give_up_reminder(reminder_id: int):
    """Потолок попыток: запись удаляется, пара остаётся в журнале как доставленная."""
    await _log_reminder(reminder_id, "sent")
    await _db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    await _db.commit()


async def delete_reminder(reminder_id: int):
    await _db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    await _db.commit()


async def reminder_pair_known(task_id: str, fire_at_utc: str) -> bool:
    """Есть ли уже такая пара в очереди или в журнале доставленных."""
    fire_at = _norm_fire_at(fire_at_utc)
    for table in ("reminders", "reminder_log"):
        async with _db.execute(
            f"SELECT 1 FROM {table} WHERE task_id = ? AND fire_at = ? LIMIT 1", (task_id, fire_at)
        ) as cursor:
            if await cursor.fetchone():
                return True
    return False


MIRROR_SOURCES = ("sync", "mstodo", "button")


async def get_sync_reminders() -> list[tuple[int, str, str]]:
    """Записи, зеркалящие MS Todo (синк, кнопка «MS Todo», «+1 ч»/«Завтра»): (id, task_id, fire_at).

    source='tg' (только Telegram) и старые записи без source сюда не попадают.
    """
    async with _db.execute(
        "SELECT id, task_id, fire_at FROM reminders "
        "WHERE source IN ('sync', 'mstodo', 'button') AND sent_at IS NULL"
    ) as cursor:
        return [(r[0], r[1], r[2]) for r in await cursor.fetchall()]


async def get_sync_state(key: str) -> str | None:
    async with _db.execute("SELECT value FROM sync_state WHERE key = ?", (key,)) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def set_sync_state(key: str, value: str):
    await _db.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value),
    )
    await _db.commit()


async def reminder_pair_logged(task_id: str, fire_at_utc: str) -> bool:
    """Пара уже доставлена (подавленные вручную пары не в счёт)."""
    async with _db.execute(
        "SELECT 1 FROM reminder_log WHERE task_id = ? AND fire_at = ? AND kind = 'sent' LIMIT 1",
        (task_id, _norm_fire_at(fire_at_utc)),
    ) as cursor:
        return await cursor.fetchone() is not None


async def prune_reminder_log(days: int = 3):
    await _db.execute("DELETE FROM reminder_log WHERE logged_at < datetime('now', ?)", (f"-{days} days",))
    await _db.commit()


async def has_task_any_reminder(task_id: str) -> bool:
    async with _db.execute(
        "SELECT 1 FROM reminders WHERE task_id = ? LIMIT 1", (task_id,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def get_reminder_by_task(task_id: str) -> dict | None:
    """Возвращает напоминание для задачи (fire_at в UTC)."""
    async with _db.execute(
        "SELECT fire_at FROM reminders WHERE task_id = ? LIMIT 1", (task_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row:
        return {"fire_at": row[0]}
    return None


async def get_task_reminders(task_id: str) -> list[tuple[int, str]]:
    """Все напоминания бота по задаче: (id, fire_at в UTC)."""
    async with _db.execute(
        "SELECT id, fire_at FROM reminders WHERE task_id = ? ORDER BY fire_at", (task_id,)
    ) as cursor:
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def set_reminder_fire_at(reminder_id: int, fire_at_utc: str):
    await _log_reminder(reminder_id)  # прежняя пара не должна вернуться из кэша MS Todo
    async with _db.execute("SELECT task_id FROM reminders WHERE id = ?", (reminder_id,)) as cursor:
        row = await cursor.fetchone()
    if row:
        await _unsuppress(row[0], _norm_fire_at(fire_at_utc))
    await _db.execute(
        "UPDATE reminders SET fire_at = ? WHERE id = ?", (_norm_fire_at(fire_at_utc), reminder_id)
    )
    await _db.commit()


async def delete_telegram_reminder_by_task(task_id: str):
    await _log_reminders("task_id = ?", (task_id,))
    await _db.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
    await _db.commit()


async def get_task_ids_with_any_reminder(task_ids: list[str]) -> set[str]:
    """Возвращает task_id-ы из переданного списка у которых есть Telegram-напоминание."""
    if not task_ids:
        return set()
    placeholders = ",".join("?" * len(task_ids))
    async with _db.execute(
        f"SELECT DISTINCT task_id FROM reminders WHERE task_id IN ({placeholders})",
        task_ids,
    ) as cursor:
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def get_all_telegram_reminder_task_ids() -> set[str]:
    async with _db.execute(
        "SELECT DISTINCT task_id FROM reminders WHERE task_id IS NOT NULL"
    ) as cursor:
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


# --- Materials («Чтение») ---

_MATERIAL_COLS = "id, title, source, created_at, read_at, pinned, length(markdown)"


def _material_row(r) -> dict:
    return {"id": r[0], "title": r[1], "source": r[2], "created_at": r[3],
            "read_at": r[4], "pinned": bool(r[5]), "chars": r[6]}


async def create_material(title: str, markdown: str, source: str, idempotency_key: str) -> tuple[int, bool]:
    """(id, duplicate). Повтор с тем же ключом возвращает прежний id и ничего не пишет.

    Вставка и поиск идут одним атомарным шагом по UNIQUE-ключу: два
    одновременных POST не получат дубль или ошибку.
    """
    cursor = await _db.execute(
        "INSERT INTO materials (title, markdown, source, created_at, idempotency_key) "
        "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?) "
        "ON CONFLICT(idempotency_key) DO NOTHING",
        (title, markdown, source, idempotency_key),
    )
    created = cursor.rowcount > 0
    await _db.commit()
    async with _db.execute("SELECT id FROM materials WHERE idempotency_key = ?", (idempotency_key,)) as cur:
        row = await cur.fetchone()
    return row[0], not created


async def list_materials() -> list[dict]:
    """Закреплённые, затем непрочитанные, затем прочитанные; внутри — новые выше."""
    async with _db.execute(
        f"SELECT {_MATERIAL_COLS} FROM materials "
        "ORDER BY pinned DESC, (read_at IS NULL) DESC, created_at DESC, id DESC"
    ) as cursor:
        return [_material_row(r) for r in await cursor.fetchall()]


async def get_material(material_id: int) -> dict | None:
    async with _db.execute(
        f"SELECT {_MATERIAL_COLS}, markdown FROM materials WHERE id = ?", (material_id,)
    ) as cursor:
        r = await cursor.fetchone()
    return {**_material_row(r), "markdown": r[7]} if r else None


async def set_material_read(material_id: int, read: bool) -> bool:
    cursor = await _db.execute(
        "UPDATE materials SET read_at = " + ("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')" if read else "NULL")
        + " WHERE id = ?", (material_id,),
    )
    await _db.commit()
    return cursor.rowcount > 0


async def set_material_pinned(material_id: int, pinned: bool) -> bool:
    cursor = await _db.execute("UPDATE materials SET pinned = ? WHERE id = ?", (int(pinned), material_id))
    await _db.commit()
    return cursor.rowcount > 0


async def delete_material(material_id: int) -> bool:
    cursor = await _db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
    await _db.commit()
    return cursor.rowcount > 0


async def count_unread_materials() -> int:
    async with _db.execute("SELECT COUNT(*) FROM materials WHERE read_at IS NULL") as cursor:
        return (await cursor.fetchone())[0]


# --- Google Calendar links ---

async def save_calendar_link(task_id: str, event_id: str):
    await _db.execute(
        "INSERT OR REPLACE INTO task_calendar_links (task_id, event_id) VALUES (?, ?)",
        (task_id, event_id),
    )
    await _db.commit()


async def get_calendar_link(task_id: str) -> str | None:
    async with _db.execute(
        "SELECT event_id FROM task_calendar_links WHERE task_id = ?", (task_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def delete_calendar_link(task_id: str) -> str | None:
    """Удаляет связь и возвращает event_id."""
    event_id = await get_calendar_link(task_id)
    if event_id:
        await _db.execute(
            "DELETE FROM task_calendar_links WHERE task_id = ?", (task_id,)
        )
        await _db.commit()
    return event_id


async def get_task_ids_in_calendar(task_ids: list[str]) -> set[str]:
    """Возвращает task_id-ы из переданного списка которые есть в task_calendar_links."""
    if not task_ids:
        return set()
    placeholders = ",".join("?" * len(task_ids))
    async with _db.execute(
        f"SELECT task_id FROM task_calendar_links WHERE task_id IN ({placeholders})",
        task_ids,
    ) as cursor:
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def get_all_calendar_task_ids() -> list[str]:
    async with _db.execute("SELECT task_id FROM task_calendar_links") as cursor:
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def get_linked_event_ids() -> set[str]:
    """События календаря, созданные из задач: в плане дня это задачи, не встречи."""
    async with _db.execute("SELECT event_id FROM task_calendar_links") as cursor:
        return {r[0] for r in await cursor.fetchall()}


# --- Ritual runs: одна запись на сводку за день ---

_RUN_TIMES = ("updated_at", "snooze_at", "done_at")


def _dt(value):
    from datetime import datetime
    return datetime.fromisoformat(value) if value else None


async def get_ritual_run(kind: str, day: str) -> dict | None:
    async with _db.execute(
        "SELECT status, updated_at, snooze_at, snoozed, message_id, done_at FROM ritual_runs WHERE kind = ? AND day = ?",
        (kind, day),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    run = dict(zip(("status", "updated_at", "snooze_at", "snoozed", "message_id", "done_at"), row))
    return {k: _dt(v) if k in _RUN_TIMES else v for k, v in run.items()}


async def claim_ritual_run(kind: str, day: str, now, expected: dict | None) -> bool:
    """Ставит status='sending', если запись не менялась с момента чтения.

    expected — запись, по которой решили слать (None — записи не было).
    Ложь — другой проход уже взял эту сводку.
    """
    if expected is None:
        cursor = await _db.execute(
            "INSERT OR IGNORE INTO ritual_runs (kind, day, status, updated_at) VALUES (?, ?, 'sending', ?)",
            (kind, day, now.isoformat()),
        )
    else:
        cursor = await _db.execute(
            "UPDATE ritual_runs SET status = 'sending', updated_at = ? "
            "WHERE kind = ? AND day = ? AND status = ? AND updated_at = ?",
            (now.isoformat(), kind, day, expected["status"], expected["updated_at"].isoformat()),
        )
    await _db.commit()
    return cursor.rowcount == 1


async def update_ritual_run(kind: str, day: str, **fields):
    """Обновляет поля записи; datetime пишутся в ISO."""
    allowed = {"status", "updated_at", "snooze_at", "snoozed", "message_id", "done_at"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"bad ritual_runs fields: {set(fields) - allowed}")
    values = [v.isoformat() if hasattr(v, "isoformat") else v for v in fields.values()]
    await _db.execute(
        f"UPDATE ritual_runs SET {', '.join(f'{k} = ?' for k in fields)} WHERE kind = ? AND day = ?",
        (*values, kind, day),
    )
    await _db.commit()


async def insert_ritual_run(kind: str, day: str, status: str, now, **fields):
    """Запись без отправки сводки (ритуал пройден до неё). Уже есть — не трогает."""
    await _db.execute(
        "INSERT OR IGNORE INTO ritual_runs (kind, day, status, updated_at) VALUES (?, ?, ?, ?)",
        (kind, day, status, now.isoformat()),
    )
    await _db.commit()
    if fields:
        await update_ritual_run(kind, day, **fields)


async def delete_ritual_run(kind: str, day: str):
    await _db.execute("DELETE FROM ritual_runs WHERE kind = ? AND day = ?", (kind, day))
    await _db.commit()


# --- Task list headers (for cleanup after done/delete) ---

async def save_task_header(key: str, chat_id: int, header_message_id: int):
    await _db.execute(
        "INSERT OR REPLACE INTO task_headers (key, chat_id, header_message_id) VALUES (?, ?, ?)",
        (key, chat_id, header_message_id),
    )
    await _db.commit()


async def remove_task_header(key: str) -> tuple[int, int] | None:
    """Удаляет запись задачи из трекинга заголовка.
    Возвращает (chat_id, header_message_id) если это была последняя задача с этим заголовком."""
    async with _db.execute(
        "SELECT chat_id, header_message_id FROM task_headers WHERE key = ?", (key,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    chat_id, header_message_id = row
    await _db.execute("DELETE FROM task_headers WHERE key = ?", (key,))
    await _db.commit()
    async with _db.execute(
        "SELECT COUNT(*) FROM task_headers WHERE header_message_id = ? AND chat_id = ?",
        (header_message_id, chat_id),
    ) as cursor:
        count_row = await cursor.fetchone()
    remaining = count_row[0] if count_row else 0
    if remaining == 0:
        return (chat_id, header_message_id)
    return None
