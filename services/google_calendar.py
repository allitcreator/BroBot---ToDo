import time
import httpx
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import config

_access_token: str | None = None
_token_expires_at: float = 0
_http_client: httpx.AsyncClient | None = None

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3"


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
        "refresh_token": config.GOOGLE_REFRESH_TOKEN,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
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


async def create_event(
    title: str,
    due_date: str,
    due_time: str,
    duration_minutes: int,
) -> dict:
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    start_dt = f"{due_date}T{due_time}:00"
    start = datetime.fromisoformat(start_dt)
    end_dt = (start + timedelta(minutes=duration_minutes)).isoformat()

    body = {
        "summary": title[:1].upper() + title[1:] if title else title,
        "start": {"dateTime": start_dt, "timeZone": config.USER_TIMEZONE},
        "end": {"dateTime": end_dt, "timeZone": config.USER_TIMEZONE},
    }

    client = get_http_client()
    resp = await client.post(
        f"{CALENDAR_URL}/calendars/{config.GOOGLE_CALENDAR_ID}/events",
        headers=headers,
        json=body,
    )
    if resp.status_code == 401:
        await _refresh_token()
        headers["Authorization"] = f"Bearer {_access_token}"
        resp = await client.post(
            f"{CALENDAR_URL}/calendars/{config.GOOGLE_CALENDAR_ID}/events",
            headers=headers,
            json=body,
        )
    resp.raise_for_status()
    return resp.json()


async def get_events_week() -> list[dict]:
    """Получает события на ближайшие 7 дней."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}

    tz = ZoneInfo(config.USER_TIMEZONE)
    now = datetime.now(tz)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59).isoformat()

    client = get_http_client()
    resp = await client.get(
        f"{CALENDAR_URL}/calendars/{config.GOOGLE_CALENDAR_ID}/events",
        headers=headers,
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "100",
        },
    )
    if resp.status_code == 401:
        await _refresh_token()
        headers["Authorization"] = f"Bearer {_access_token}"
        resp = await client.get(
            f"{CALENDAR_URL}/calendars/{config.GOOGLE_CALENDAR_ID}/events",
            headers=headers,
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "100",
            },
        )
    resp.raise_for_status()
    return resp.json().get("items", [])
