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


def _list_path() -> str:
    return f"/me/todo/lists/{config.MS_TODO_LIST_ID}/tasks"


def _task_path(task_id: str) -> str:
    return f"/me/todo/lists/{config.MS_TODO_LIST_ID}/tasks/{task_id}"


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


async def create_task(title: str, due_date: str, due_time: str | None = None, subtasks: list[str] | None = None, description: str | None = None) -> dict:
    if due_time:
        dt_str = f"{due_date}T{due_time}:00"
        tz_str = config.USER_TIMEZONE
    else:
        dt_str = f"{due_date}T20:00:00"
        tz_str = "UTC"
    body = {
        "title": title[:1].upper() + title[1:] if title else title,
        "dueDateTime": {
            "dateTime": dt_str,
            "timeZone": tz_str,
        },
        "importance": "normal",
        "status": "notStarted",
    }
    if description:
        body["body"] = {"content": description, "contentType": "text"}
    task = await _request("POST", _list_path(), json=body)

    if subtasks:
        for item in subtasks:
            await add_checklist_item(task["id"], item)

    return task


async def add_checklist_item(task_id: str, text: str):
    await _request(
        "POST",
        f"{_task_path(task_id)}/checklistItems",
        json={"displayName": text, "isChecked": False},
    )


async def complete_task(task_id: str):
    await _request("PATCH", _task_path(task_id), json={"status": "completed"})


async def delete_task(task_id: str):
    await _request("DELETE", _task_path(task_id))


async def get_task(task_id: str) -> dict:
    return await _request("GET", _task_path(task_id))


async def remove_reminder(task_id: str):
    await _request("PATCH", _task_path(task_id), json={"isReminderOn": False})


async def set_reminder(task_id: str, fire_at_utc: str):
    """Устанавливает напоминание в MS Todo. fire_at_utc — ISO datetime в UTC."""
    await _request("PATCH", _task_path(task_id), json={
        "isReminderOn": True,
        "reminderDateTime": {
            "dateTime": fire_at_utc,
            "timeZone": "UTC",
        },
    })


async def update_task(task_id: str, title: str | None = None, due_date: str | None = None):
    body = {}
    if title:
        body["title"] = title
    if due_date:
        body["dueDateTime"] = {
            "dateTime": f"{due_date}T20:00:00",
            "timeZone": "UTC",
        }
    if body:
        await _request("PATCH", _task_path(task_id), json=body)


async def get_tasks(odata_filter: str | None = None) -> list[dict]:
    params = {"$top": "100"}
    if odata_filter:
        params["$filter"] = odata_filter
    data = await _request("GET", _list_path(), params=params)
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
    today = date.today().isoformat()
    tasks = await get_tasks("status ne 'completed'")
    return [t for t in tasks if _task_local_date(t) == today]


async def get_tasks_tomorrow() -> list[dict]:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    tasks = await get_tasks("status ne 'completed'")
    return [t for t in tasks if _task_local_date(t) == tomorrow]


async def get_all_tasks() -> list[dict]:
    return await get_tasks("status ne 'completed'")


async def get_overdue_tasks() -> list[dict]:
    today = date.today().isoformat()
    tasks = await get_tasks("status ne 'completed'")
    local_date = _task_local_date  # avoid repeated lookup
    return [t for t in tasks if (d := local_date(t)) is not None and d < today]


async def get_stats() -> dict:
    today = date.today().isoformat()

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
