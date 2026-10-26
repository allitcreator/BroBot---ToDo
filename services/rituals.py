"""Ритуалы разбора: утренняя и вечерняя сводки в чат.

Сводка уходит, только если есть что решать. Одна запись `ritual_runs` на
сводку за день: её ставят до отправки (status='sending'), поэтому рестарт
не даёт дубля. Сбой MS To Do или Telegram помечает запись как failed, и
отправка повторяется не чаще раза в 5 минут в пределах окна 2 часа.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config
from db import storage
from services import google_calendar, ms_todo

logger = logging.getLogger(__name__)

MORNING, EVENING = "morning", "evening"
KINDS = (MORNING, EVENING)
WINDOW = timedelta(hours=2)    # опоздание после рестарта или сбоя
RETRY = timedelta(minutes=5)   # пауза между попытками после сбоя
STALE = timedelta(minutes=10)  # 'sending' дольше — процесс упал посреди отправки
SNOOZE = timedelta(hours=1)
STUCK_LIMIT = 3


# --- когда слать ---

def due_rituals(now: datetime, settings: dict, runs: dict) -> list[str]:
    """Какие сводки пора отправить. now — местное aware-время.

    runs[kind] — запись ritual_runs за сегодня или None. Без сети и БД.
    """
    if now.weekday() >= 5 and not settings["weekend_rituals"]:
        return []
    due = []
    for kind in KINDS:
        if not settings[f"{kind}_enabled"]:
            continue
        start = datetime.combine(now.date(), time.fromisoformat(settings[f"{kind}_time"]), tzinfo=now.tzinfo)
        run = runs.get(kind)
        # После «Позже» окно отсчитывается от времени повтора.
        end = (run["snooze_at"] if run and run["snoozed"] else start) + WINDOW
        if run is None:
            ok = start <= now < end
        elif run["status"] == "snoozed":
            ok = run["done_at"] is None and run["snooze_at"] <= now < end
        elif run["status"] == "failed":
            ok = now < end and now - run["updated_at"] >= RETRY
        elif run["status"] == "sending":
            ok = now < end and now - run["updated_at"] >= STALE
        else:  # sent, skipped, dismissed, done
            ok = False
        if ok:
            due.append(kind)
    return due


# --- срезы задач ---

def _created_date(task: dict) -> date | None:
    raw = task.get("createdDateTime")
    if not raw:
        return None
    dt = datetime.fromisoformat(raw.split(".")[0].rstrip("Z")).replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(config.USER_TIMEZONE)).date()


def morning_slices(tasks: list[dict], today: date, stuck_days: int) -> dict:
    """Просрочено / застряло / входящие / на сегодня для утреннего разбора.

    Застряло — создана stuck_days дней назад или раньше, без даты или с
    просроченной датой; до трёх самых старых. Они не повторяются в
    «Просрочено» и «Входящих».
    """
    today_iso = today.isoformat()
    border = today - timedelta(days=stuck_days)
    by_created = lambda t: t.get("createdDateTime") or ""
    dated = [(ms_todo.task_local_date(t), t) for t in tasks]

    candidates = [
        t for d, t in dated
        if (d is None or d < today_iso) and (c := _created_date(t)) is not None and c <= border
    ]
    stuck = sorted(candidates, key=by_created)[:STUCK_LIMIT]
    stuck_ids = {t["id"] for t in stuck}

    overdue = sorted(
        ((d, t) for d, t in dated if d and d < today_iso and t["id"] not in stuck_ids),
        key=lambda p: (p[0], by_created(p[1])),
    )
    return {
        "overdue": [t for _, t in overdue],
        "stuck": stuck,
        "inbox": sorted((t for d, t in dated if d is None and t["id"] not in stuck_ids), key=by_created),
        "today": sorted((t for d, t in dated if d == today_iso), key=by_created),
    }


def meetings(events: list[dict], linked: set[str]) -> list[dict]:
    """Встречи дня: события календаря, кроме созданных из задач."""
    return [e for e in events if e.get("id") not in linked and e.get("status") != "cancelled"]


# --- тексты ---

def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


_STUCK_PERIOD = {7: "недели", 14: "2 недель", 30: "месяца"}


def morning_text(*, today: int, meetings: int | None, overdue: int, stuck: int, inbox: int, stuck_days: int) -> str:
    parts = []
    if today:
        parts.append(f"{today} {plural(today, 'задача', 'задачи', 'задач')}")
    if meetings:
        parts.append(f"{meetings} {plural(meetings, 'встреча', 'встречи', 'встреч')}")
    lines = ["☀️ Доброе утро!" + (f" Сегодня {' и '.join(parts)}." if parts else ""), "Ждут решения:"]
    if overdue:
        lines.append(f"• {overdue} {plural(overdue, 'просрочена', 'просрочены', 'просрочено')}")
    if stuck:
        period = _STUCK_PERIOD.get(stuck_days, f"{stuck_days} дней")
        verb = plural(stuck, "застряла (висит", "застряли (висят", "застряли (висят")
        lines.append(f"• {stuck} {verb} больше {period})")
    if inbox:
        lines.append(f"• {inbox} во входящих")
    lines.append("Займёт пару минут.")
    return "\n".join(lines)


def evening_text(*, done: int, left: int) -> str:
    return f"🌙 Закрываем день? Сделано {done} из {done + left}.\nОсталось {left} — отметим и перенесём, 30 секунд."


def ritual_url(base: str, ritual: str) -> str:
    """Адрес мини-аппа с ?ritual=…; прочие параметры base сохраняются.

    `startapp` работает только для ссылок t.me/<бот>/<апп>, а у нас
    web_app-кнопка — поэтому параметр в query.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "ritual"] + [("ritual", ritual)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def keyboard(kind: str, day: str | None, later: bool = True) -> InlineKeyboardMarkup:
    """«Разобрать ↗» / «Закрыть день ↗» и, если можно, «Позже» / «Не сегодня».

    day=None — сводка по команде /ritual, без записи: второй кнопки нет.
    """
    open_text = "Разобрать ↗" if kind == MORNING else "Закрыть день ↗"
    row = [InlineKeyboardButton(text=open_text, web_app=WebAppInfo(url=ritual_url(config.MINIAPP_URL, kind)))]
    if later and day:
        if kind == MORNING:
            row.append(InlineKeyboardButton(text="Позже", callback_data=f"ritual:snooze:{kind}:{day}"))
        else:
            row.append(InlineKeyboardButton(text="Не сегодня", callback_data=f"ritual:dismiss:{kind}:{day}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def build_text(kind: str, settings: dict, today: date) -> str | None:
    """Текст сводки; None — решать нечего и слать не нужно."""
    tasks = await ms_todo.get_all_tasks()
    s = morning_slices(tasks, today, settings["stuck_days"])
    if kind == MORNING:
        if not (s["overdue"] or s["stuck"] or s["inbox"]):
            return None
        try:
            count = len(meetings(await google_calendar.get_events_day(today), await storage.get_linked_event_ids()))
        except Exception:
            # Календарь — украшение сводки, без него она всё равно нужна.
            logger.warning("rituals: calendar unavailable", exc_info=True)
            count = None
        return morning_text(
            today=len(s["today"]), meetings=count, overdue=len(s["overdue"]),
            stuck=len(s["stuck"]), inbox=len(s["inbox"]), stuck_days=settings["stuck_days"],
        )
    if not s["today"]:
        return None
    done = len(await ms_todo.get_completed_since(today))
    return evening_text(done=done, left=len(s["today"]))


# --- отправка ---

async def _send(bot, kind: str, day: str, run: dict | None, settings: dict, now: datetime):
    if not await storage.claim_ritual_run(kind, day, now, run):
        return
    later = not (run and run["snoozed"])
    try:
        text = await build_text(kind, settings, now.date())
    except Exception:
        logger.warning("rituals: %s summary failed", kind, exc_info=True)
        await storage.update_ritual_run(kind, day, status="failed", updated_at=now)
        return
    if text is None:
        await storage.update_ritual_run(kind, day, status="skipped", updated_at=now)
        return
    try:
        msg = await bot.send_message(config.TELEGRAM_USER_ID, text, reply_markup=keyboard(kind, day, later))
    except Exception:
        logger.warning("rituals: %s send failed", kind, exc_info=True)
        await storage.update_ritual_run(kind, day, status="failed", updated_at=now)
        return
    await storage.update_ritual_run(kind, day, status="sent", updated_at=now, message_id=msg.message_id)
    logger.info("rituals: %s summary sent", kind)


async def tick(bot, now: datetime | None = None):
    """Один проход планировщика: шлёт сводки, которым пора."""
    now = now or datetime.now(ZoneInfo(config.USER_TIMEZONE))
    settings = await storage.get_ritual_settings(config.TELEGRAM_USER_ID)
    day = now.date().isoformat()
    runs = {kind: await storage.get_ritual_run(kind, day) for kind in KINDS}
    for kind in due_rituals(now, settings, runs):
        await _send(bot, kind, day, runs[kind], settings, now)


async def _delete_summary(bot, run: dict | None):
    if run and run["message_id"]:
        try:
            await bot.delete_message(config.TELEGRAM_USER_ID, run["message_id"])
        except Exception:
            # Старше 48 часов или уже удалено — не страшно.
            logger.info("rituals: summary message not deleted", exc_info=True)


async def snooze(bot, kind: str, day: str, now: datetime):
    """«Позже»: убрать сводку и прислать её ещё раз через час, один раз."""
    run = await storage.get_ritual_run(kind, day)
    if not run or run["status"] != "sent" or run["snoozed"]:
        return
    await _delete_summary(bot, run)
    await storage.update_ritual_run(
        kind, day, status="snoozed", updated_at=now, snooze_at=now + SNOOZE, snoozed=1, message_id=None,
    )


async def dismiss(bot, kind: str, day: str):
    """«Не сегодня»: убрать сводку и молчать до завтра."""
    run = await storage.get_ritual_run(kind, day)
    if not run:
        return
    await _delete_summary(bot, run)
    await storage.update_ritual_run(kind, day, status="dismissed", message_id=None)


async def mark_done(kind: str, day: str, now: datetime):
    """Ритуал пройден в мини-аппе: повтора по «Позже» не будет, а если сводки
    ещё не было — она и не придёт."""
    if await storage.get_ritual_run(kind, day):
        await storage.update_ritual_run(kind, day, done_at=now)
    else:
        await storage.insert_ritual_run(kind, day, "done", now, done_at=now)


async def send_now(bot, kind: str) -> bool:
    """Сводка по команде /ritual: без записи и без «Позже». Ложь — решать нечего."""
    settings = await storage.get_ritual_settings(config.TELEGRAM_USER_ID)
    text = await build_text(kind, settings, config.local_today())
    if text is None:
        return False
    await bot.send_message(config.TELEGRAM_USER_ID, text, reply_markup=keyboard(kind, None))
    return True
