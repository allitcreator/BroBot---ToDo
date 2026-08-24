import base64
import time
import httpx
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
import config

_access_token: str | None = None
_token_expires_at: float = 0
_http_client: httpx.AsyncClient | None = None

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_URL = "https://graph.microsoft.com/v1.0"


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def close():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


async def _refresh_token() -> str:
    global _access_token, _token_expires_at
    client = get_http_client()
    resp = await client.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": config.MS_REFRESH_TOKEN,
        "client_id": config.MS_CLIENT_ID,
        "client_secret": config.MS_CLIENT_SECRET,
        "scope": "Tasks.ReadWrite offline_access User.Read",
    })
    resp.raise_for_status()
    data = resp.json()
    _access_token = data["access_token"]
    _token_expires_at = time.time() + data.get("expires_in", 3600)
    return _access_token


async def _get_token() -> str:
    if not _access_token or time.time() > _token_expires_at - 60:
        await _refresh_token()
    return _access_token


async def _request(method: str, path: str, params: dict | None = None, **kwargs):
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    client = get_http_client()
    resp = await client.request(method, f"{GRAPH_URL}{path}", headers=headers, params=params, **kwargs)
    if resp.status_code == 401:
        await _refresh_token()
        headers["Authorization"] = f"Bearer {_access_token}"
        resp = await client.request(method, f"{GRAPH_URL}{path}", headers=headers, params=params, **kwargs)
    if not resp.is_success:
        raise Exception(f"MS Graph {resp.status_code}: {resp.text}")
    if resp.content:
        return resp.json()
    return None


def _list_path(list_id: str | None = None) -> str:
    return f"/me/todo/lists/{list_id or config.MS_TODO_LIST_ID}/tasks"


def _task_path(task_id: str, list_id: str | None = None) -> str:
    return f"/me/todo/lists/{list_id or config.MS_TODO_LIST_ID}/tasks/{task_id}"


def format_due_date_from_task(task: dict) -> str:
    """Форматирует дату задачи для отображения (DD.MM.YYYY)."""
    due = task.get("dueDateTime")
    if not due:
        return "без даты"
    dt_str = due.get("dateTime", "")
    if not dt_str:
        return "без даты"
    try:
        dt_str = dt_str.split(".")[0]
        tz_str = due.get("timeZone", "UTC")
        tz = ZoneInfo(tz_str) if tz_str != "UTC" else ZoneInfo("UTC")
        dt = datetime.fromisoformat(dt_str).replace(tzinfo=tz)
        local_dt = dt.astimezone(ZoneInfo(config.USER_TIMEZONE))
        return local_dt.strftime("%d.%m.%Y")
    except Exception:
        return dt_str[:10]


async def create_task(title: str, due_date: str | None = None, due_time: str | None = None, subtasks: list[str] | None = None, description: str | None = None, list_id: str | None = None) -> dict:
    body = {
        "title": title[:1].upper() + title[1:] if title else title,
        "importance": "normal",
        "status": "notStarted",
    }
    # Проекты создаются без даты — у них нет срока, это не задача дня.
    if due_date:
        if due_time:
            dt_str = f"{due_date}T{due_time}:00"
            tz_str = config.USER_TIMEZONE
        else:
            dt_str = f"{due_date}T20:00:00"
            tz_str = "UTC"
        body["dueDateTime"] = {"dateTime": dt_str, "timeZone": tz_str}
    if description:
        body["body"] = {"content": description, "contentType": "text"}
    task = await _request("POST", _list_path(list_id), json=body)

    if subtasks:
        for item in subtasks:
            await add_checklist_item(task["id"], item, list_id)

    return task


async def add_checklist_item(task_id: str, text: str, list_id: str | None = None):
    await _request(
        "POST",
        f"{_task_path(task_id, list_id)}/checklistItems",
        json={"displayName": text, "isChecked": False},
    )


async def get_checklist_items(task_id: str, list_id: str | None = None) -> list[dict]:
    data = await _request("GET", f"{_task_path(task_id, list_id)}/checklistItems")
    return data.get("value", []) if data else []


async def update_task_body(task_id: str, content: str, list_id: str | None = None):
    """Перезаписывает заметку задачи целиком (в MS Todo заметка — plain text)."""
    await _request("PATCH", _task_path(task_id, list_id), json={
        "body": {"content": content, "contentType": "text"},
    })


async def list_attachments(task_id: str, list_id: str | None = None) -> list[dict]:
    data = await _request("GET", f"{_task_path(task_id, list_id)}/attachments")
    return data.get("value", []) if data else []


async def add_attachment(task_id: str, name: str, content_bytes: bytes, list_id: str | None = None) -> dict:
    """Прикладывает файл к задаче. Годится для мелких файлов (бриф — всегда мелкий)."""
    return await _request("POST", f"{_task_path(task_id, list_id)}/attachments", json={
        "@odata.type": "#microsoft.graph.taskFileAttachment",
        "name": name,
        "contentType": "text/markdown",
        "contentBytes": base64.b64encode(content_bytes).decode(),
    })


async def delete_attachment(task_id: str, attachment_id: str, list_id: str | None = None):
    await _request("DELETE", f"{_task_path(task_id, list_id)}/attachments/{attachment_id}")


async def replace_attachment(task_id: str, name: str, content_bytes: bytes, list_id: str | None = None) -> dict:
    """Вложение в Graph нельзя обновить — только снести одноимённое и создать заново."""
    try:
        for att in await list_attachments(task_id, list_id):
            if att.get("name") == name:
                await delete_attachment(task_id, att["id"], list_id)
    except Exception:
        pass
    return await add_attachment(task_id, name, content_bytes, list_id)


async def complete_task(task_id: str, list_id: str | None = None):
    await _request("PATCH", _task_path(task_id, list_id), json={"status": "completed"})


async def delete_task(task_id: str, list_id: str | None = None):
    await _request("DELETE", _task_path(task_id, list_id))


async def get_task(task_id: str, list_id: str | None = None) -> dict:
    return await _request("GET", _task_path(task_id, list_id))


async def remove_reminder(task_id: str):
    # Одного isReminderOn=false мало: Graph его молча игнорирует и напоминание
    # остаётся. Снимается только вместе со сбросом самой даты.
    await _request("PATCH", _task_path(task_id), json={
        "isReminderOn": False,
        "reminderDateTime": None,
    })


async def set_reminder(task_id: str, fire_at_utc: str):
    """Устанавливает напоминание в MS Todo. fire_at_utc — ISO datetime в UTC."""
    await _request("PATCH", _task_path(task_id), json={
        "isReminderOn": True,
        "reminderDateTime": {
            "dateTime": fire_at_utc,
            "timeZone": "UTC",
        },
    })


async def update_task(task_id: str, title: str | None = None, due_date: str | None = None, list_id: str | None = None):
    body = {}
    if title:
        body["title"] = title
    if due_date:
        body["dueDateTime"] = {
            "dateTime": f"{due_date}T20:00:00",
            "timeZone": "UTC",
        }
    if body:
        await _request("PATCH", _task_path(task_id, list_id), json=body)


async def _reschedule_task(task: dict, new_date: str):
    """Переносит задачу на new_date (YYYY-MM-DD), сохраняя время задачи."""
    due = task.get("dueDateTime") or {}
    dt_str = (due.get("dateTime") or "").split(".")[0]
    try:
        tz = ZoneInfo(due.get("timeZone", "UTC"))
        local_dt = datetime.fromisoformat(dt_str).replace(tzinfo=tz).astimezone(ZoneInfo(config.USER_TIMEZONE))
        body = {"dateTime": f"{new_date}T{local_dt.strftime('%H:%M:%S')}", "timeZone": config.USER_TIMEZONE}
    except Exception:
        body = {"dateTime": f"{new_date}T20:00:00", "timeZone": "UTC"}
    await _request("PATCH", _task_path(task["id"]), json={"dueDateTime": body})


async def move_today_to_tomorrow() -> list[dict]:
    """Переносит все незавершённые задачи с сегодня на завтра."""
    tasks = await get_tasks_today()
    new_date = (config.local_today() + timedelta(days=1)).isoformat()
    for t in tasks:
        await _reschedule_task(t, new_date)
    return tasks


async def move_overdue_to_today() -> list[dict]:
    """Переносит все просроченные задачи на сегодня."""
    tasks = await get_overdue_tasks()
    new_date = config.local_today().isoformat()
    for t in tasks:
        await _reschedule_task(t, new_date)
    return tasks


async def get_tasks(odata_filter: str | None = None, list_id: str | None = None) -> list[dict]:
    params = {"$top": "100"}
    if odata_filter:
        params["$filter"] = odata_filter
    data = await _request("GET", _list_path(list_id), params=params)
    return data.get("value", [])


def _task_local_date(task: dict) -> str | None:
    """Возвращает дату задачи в локальном timezone (YYYY-MM-DD)."""
    due = task.get("dueDateTime")
    if not due:
        return None
    dt_str = due.get("dateTime", "")
    if not dt_str:
        return None
    try:
        dt_str = dt_str.split(".")[0]
        tz_str = due.get("timeZone", "UTC")
        tz = ZoneInfo(tz_str) if tz_str != "UTC" else ZoneInfo("UTC")
        dt = datetime.fromisoformat(dt_str).replace(tzinfo=tz)
        local_dt = dt.astimezone(ZoneInfo(config.USER_TIMEZONE))
        return local_dt.date().isoformat()
    except Exception:
        return dt_str[:10]


async def get_tasks_today() -> list[dict]:
    today = config.local_today().isoformat()
    tasks = await get_tasks("status ne 'completed'")
    return [t for t in tasks if _task_local_date(t) == today]


async def get_tasks_tomorrow() -> list[dict]:
    tomorrow = (config.local_today() + timedelta(days=1)).isoformat()
    tasks = await get_tasks("status ne 'completed'")
    return [t for t in tasks if _task_local_date(t) == tomorrow]


async def get_all_tasks(list_id: str | None = None) -> list[dict]:
    return await get_tasks("status ne 'completed'", list_id)


async def get_overdue_tasks() -> list[dict]:
    today = config.local_today().isoformat()
    tasks = await get_tasks("status ne 'completed'")
    local_date = _task_local_date  # avoid repeated lookup
    return [t for t in tasks if (d := local_date(t)) is not None and d < today]


async def get_stats() -> dict:
    today = config.local_today().isoformat()

    # Два отдельных запроса чтобы не упираться в $top=100
    open_tasks_list = await get_tasks("status ne 'completed'")
    completed_list = await get_tasks("status eq 'completed'")

    open_tasks = len(open_tasks_list)
    overdue = sum(1 for t in open_tasks_list if (d := _task_local_date(t)) and d < today)
    created_today = sum(
        1 for t in open_tasks_list + completed_list
        if (t.get("createdDateTime") or "")[:10] == today
    )
    completed_today = sum(
        1 for t in completed_list
        if ((t.get("completedDateTime") or {}).get("dateTime", ""))[:10] == today
    )

    return {
        "completed_today": completed_today,
        "created_today": created_today,
        "open_tasks": open_tasks,
        "overdue": overdue,
    }
