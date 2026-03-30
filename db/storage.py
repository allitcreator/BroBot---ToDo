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
    await _db.commit()


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


# --- Task ID registry (persistent) ---

async def resolve_task_id(key: str) -> str | None:
    async with _db.execute(
        "SELECT full_id FROM task_ids WHERE key = ?", (key,)
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None
