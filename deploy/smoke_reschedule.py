"""Сквозной смоук переноса задачи со временем — на проде, за собой убирает.

Создаёт задачу «[смоук ToDoBOT] удалить» через месяц с событием в Google
Calendar на 03:00 и напоминаниями бота и MS To Do за 15 минут, переносит
её через API мини-аппа (подписанный initData) и сверяет Graph, Google и
БД. В конце удаляет задачу, событие и напоминания. Названия чужих задач и
токены не выводит.

Запуск:
    ssh vps4 "cd ~/todobot && docker compose exec -T todobot python -" < deploy/smoke_reschedule.py
"""
import asyncio
import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

import config
from db import storage
from services import google_calendar, ms_todo

TZ = ZoneInfo(config.USER_TIMEZONE)
D = config.local_today() + timedelta(days=30)
TITLE = "[смоук ToDoBOT] удалить"
results = []


def auth() -> dict:
    f = {"auth_date": str(int(time.time())), "query_id": "smoke",
         "user": json.dumps({"id": config.TELEGRAM_USER_ID}, separators=(",", ":"))}
    check_str = "\n".join(f"{k}={f[k]}" for k in sorted(f))
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(f)}


def utc(day, hm: str) -> str:
    local = datetime.combine(day, datetime.strptime(hm, "%H:%M").time(), tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def check(name: str, ok: bool, got=""):
    results.append(bool(ok))
    print("OK  " if ok else "FAIL", name, "" if ok else got)


async def main():
    await storage.init_db()
    api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{config.WEBAPP_PORT}", timeout=60)
    task_id = event_id = None
    try:
        task_id = (await ms_todo.create_task(TITLE, due_date=D.isoformat()))["id"]
        event_id = (await google_calendar.create_event(TITLE, D.isoformat(), "03:00", 30))["id"]
        await storage.save_calendar_link(task_id, event_id)
        await storage.save_reminder(config.TELEGRAM_USER_ID, utc(D, "02:45"), "⏰ " + TITLE, task_id=task_id)
        await ms_todo.set_reminder(task_id, utc(D, "02:45"))
        key = await storage.register_task_id(task_id)

        r = await api.get(f"/api/tasks/{key}/time", headers=auth())
        check("время задачи = начало события 03:00", r.json() == {"time": "03:00"}, r.text)

        d1 = D + timedelta(days=1)
        r = await api.post(f"/api/tasks/{key}/schedule", json={"date": d1.isoformat(), "time": "04:30"}, headers=auth())
        check("перенос на D+1 04:30 без предупреждений", r.status_code == 200 and "warnings" not in r.json(), r.text)
        ev = await google_calendar.get_event(event_id)
        start = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
        end = datetime.fromisoformat(ev["end"]["dateTime"]).astimezone(TZ)
        check("событие 04:30–05:00 в новый день",
              (start.date(), start.strftime("%H:%M"), end.strftime("%H:%M")) == (d1, "04:30", "05:00"), (start, end))
        tg = await storage.get_task_reminders(task_id)
        check("напоминание бота 04:15", [f for _, f in tg] == [utc(d1, "04:15")], tg)
        t = await ms_todo.get_task(task_id)
        ms_fire = (t.get("reminderDateTime") or {}).get("dateTime", "")[:19]
        check("напоминание MS To Do 04:15", t.get("isReminderOn") and ms_fire == utc(d1, "04:15"),
              (t.get("isReminderOn"), ms_fire))
        check("дата задачи D+1", ms_todo.task_local_date(t) == d1.isoformat(), t.get("dueDateTime"))

        d2 = D + timedelta(days=2)
        r = await api.post(f"/api/tasks/{key}/schedule", json={"date": d2.isoformat(), "time": None}, headers=auth())
        check("перенос без времени", r.status_code == 200, r.text)
        check("напоминание бота снято", await storage.get_task_reminders(task_id) == [])
        check("связь с событием снята", await storage.get_calendar_link(task_id) is None)
        try:
            gone = (await google_calendar.get_event(event_id)).get("status") == "cancelled"
        except httpx.HTTPStatusError as e:
            gone = e.response.status_code in (404, 410)
        check("событие удалено", gone)
        if gone:
            event_id = None
        t = await ms_todo.get_task(task_id)
        check("напоминание MS снято", not t.get("isReminderOn"), t.get("isReminderOn"))
        check("дата задачи D+2", ms_todo.task_local_date(t) == d2.isoformat(), t.get("dueDateTime"))
    finally:
        if task_id:
            await storage.delete_telegram_reminder_by_task(task_id)
            await storage.delete_calendar_link(task_id)
            await ms_todo.delete_task(task_id)
        if event_id:
            await google_calendar.delete_event(event_id)
        await api.aclose()
        await ms_todo.close()
        await google_calendar.close()
        await storage.close_db()
        print("уборка: задача", "удалена" if task_id else "не создана", "| событие", "удалено")
    print(f"ИТОГ: {sum(results)}/{len(results)}")
    if not all(results):
        sys.exit(1)


asyncio.run(main())
