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
    await _db.commit()

    # Migrate: add task_id column to reminders if it doesn't exist yet
    try:
        await _db.execute("ALTER TABLE reminders ADD COLUMN task_id TEXT")
        await _db.commit()
    except Exception:
        pass  # column already exists


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


async def set_confirm_mode(user_id: int, mode: str):
    await _db.execute(
        "INSERT INTO settings (user_id, confirm_mode) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET confirm_mode = excluded.confirm_mode",
        (user_id, mode),
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

async def save_reminder(chat_id: int, fire_at_utc: str, text: str, task_id: str | None = None):
    """fire_at_utc — ISO datetime без timezone, всегда UTC."""
    dt_str = fire_at_utc.replace("+00:00", "").split(".")[0]
    await _db.execute(
        "INSERT INTO reminders (chat_id, fire_at, text, task_id) VALUES (?, ?, ?, ?)",
        (chat_id, dt_str, text, task_id),
    )
    await _db.commit()


async def get_due_reminders() -> list[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    async with _db.execute(
        "SELECT id, chat_id, text FROM reminders WHERE fire_at <= ?", (now,)
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"id": r[0], "chat_id": r[1], "text": r[2]} for r in rows]


async def delete_reminder(reminder_id: int):
    await _db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
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


async def delete_telegram_reminder_by_task(task_id: str):
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
