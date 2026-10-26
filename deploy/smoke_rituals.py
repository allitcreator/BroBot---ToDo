"""Смоук ритуалов на проде: настройки, API разбора, планировщик сводок.

Пишет только служебные записи ritual_runs на далёкую дату и удаляет их;
сообщения в чат не шлёт — вместо бота подставлен фейковый. Данные MS To Do
и Google читает настоящие. Названия задач не выводит.

Запуск:
    ssh vps4 "cd ~/todobot && docker compose exec -T todobot python -" < deploy/smoke_rituals.py
"""
import asyncio
import hashlib
import hmac
import json
import sys
import time as time_mod
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

import config
from db import storage
from services import google_calendar, ms_todo, rituals

TZ = ZoneInfo(config.USER_TIMEZONE)
results = []


def auth() -> dict:
    f = {"auth_date": str(int(time_mod.time())), "query_id": "smoke",
         "user": json.dumps({"id": config.TELEGRAM_USER_ID}, separators=(",", ":"))}
    check_str = "\n".join(f"{k}={f[k]}" for k in sorted(f))
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(f)}


def check(name: str, ok: bool, got=""):
    results.append(bool(ok))
    print("OK  " if ok else "FAIL", name, "" if ok else got)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=1)

    async def delete_message(self, chat_id, message_id):
        pass


async def main():
    await storage.init_db()
    api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{config.WEBAPP_PORT}", timeout=60)
    # Далёкий вторник: записи ritual_runs не пересекаются с настоящими.
    day = config.local_today() + timedelta(days=63)
    day += timedelta(days=(1 - day.weekday()) % 7)
    try:
        r = await api.get("/api/settings", headers=auth())
        s = r.json()
        check("настройки ритуалов в API", r.status_code == 200 and "morning_time" in s and "stuck_days" in s, r.text[:200])

        t0 = time_mod.monotonic()
        r = await api.get("/api/ritual/morning", headers=auth())
        m = r.json()
        check("шаги утреннего разбора", r.status_code == 200 and set(m) == {"overdue", "stuck", "inbox"}, r.text[:200])
        keys = [t["key"] for k in m for t in m[k]]
        check("задача не в двух шагах", len(keys) == len(set(keys)))
        print(f"     просрочено {len(m['overdue'])}, застряло {len(m['stuck'])}, входящих {len(m['inbox'])}, "
              f"{time_mod.monotonic() - t0:.1f} с")

        t0 = time_mod.monotonic()
        r = await api.get("/api/ritual/plan", headers=auth())
        p = r.json()
        check("план дня", r.status_code == 200 and "today" in p and "norm" in p, r.text[:200])
        print(f"     встреч {None if p['events'] is None else len(p['events'])}, задач {len(p['today'])}, "
              f"норма {p['norm']}, {time_mod.monotonic() - t0:.1f} с")

        settings = {**await storage.get_ritual_settings(config.TELEGRAM_USER_ID),
                    "morning_enabled": True, "evening_enabled": True}
        for kind in rituals.KINDS:
            text = await rituals.build_text(kind, settings, config.local_today())
            print(f"     сводка {kind} сегодня: {'нечего решать' if text is None else text.splitlines()[0]}")

        bot = FakeBot()
        for kind in rituals.KINDS:
            at = datetime.combine(day, datetime.strptime(settings[f"{kind}_time"], "%H:%M").time(), tzinfo=TZ)
            runs = {k: await storage.get_ritual_run(k, day.isoformat()) for k in rituals.KINDS}
            due = rituals.due_rituals(at, settings, runs)
            check(f"{kind}: пора слать в {at:%H:%M}", kind in due, due)
            await rituals._send(bot, kind, day.isoformat(), runs[kind], settings, at)
            run = await storage.get_ritual_run(kind, day.isoformat())
            check(f"{kind}: запись поставлена ({run and run['status']})", run and run["status"] in ("sent", "skipped"))
            runs = {k: await storage.get_ritual_run(k, day.isoformat()) for k in rituals.KINDS}
            check(f"{kind}: повтор не нужен", kind not in rituals.due_rituals(at + timedelta(minutes=1), settings, runs))
        for _, text, markup in bot.sent:
            url = markup.inline_keyboard[0][0].web_app.url
            check("кнопка ведёт в разбор", "ritual=" in url, url)
        print(f"     сводок отправлено фейковому боту: {len(bot.sent)}")
    finally:
        for kind in rituals.KINDS:
            await storage.delete_ritual_run(kind, day.isoformat())
        await api.aclose()
        await ms_todo.close()
        await google_calendar.close()
        await storage.close_db()
    print(f"ИТОГ: {sum(results)}/{len(results)}")
    if not all(results):
        sys.exit(1)


asyncio.run(main())
