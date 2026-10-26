import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
MS_REFRESH_TOKEN = os.getenv("MS_REFRESH_TOKEN")
MS_TODO_LIST_ID = os.getenv("MS_TODO_LIST_ID")
MS_PROJECTS_LIST_ID = os.getenv("MS_PROJECTS_LIST_ID")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

DB_PATH = os.getenv("DB_PATH", "data/bot.db")
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "Europe/Moscow")

# Потолок вопросов в интервью по проекту
INTERVIEW_MAX_QUESTIONS = int(os.getenv("INTERVIEW_MAX_QUESTIONS", "20"))

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


def local_today() -> date:
    """Текущая дата в часовом поясе пользователя."""
    return datetime.now(ZoneInfo(USER_TIMEZONE)).date()


_REQUIRED_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "OPENROUTER_API_KEY",
    "MS_CLIENT_ID",
    "MS_CLIENT_SECRET",
    "MS_REFRESH_TOKEN",
    "MS_TODO_LIST_ID",
)


def config_problems() -> list[str]:
    """Что не так с окружением. Пустой список — можно стартовать.

    Без проверки бот поднимался с пустыми переменными и падал позже, на
    первом запросе пользователя, а пустой WEBHOOK_SECRET молча выключал
    проверку подписи Telegram.
    """
    problems = [f"не задан {name}" for name in _REQUIRED_VARS if not globals()[name]]
    if not TELEGRAM_USER_ID:
        problems.append("не задан TELEGRAM_USER_ID — бот никому не ответит")
    if USE_WEBHOOK:
        if not WEBHOOK_HOST:
            problems.append("USE_WEBHOOK=true, но не задан WEBHOOK_HOST")
        if len(WEBHOOK_SECRET or "") < 32:
            problems.append(
                "USE_WEBHOOK=true, но WEBHOOK_SECRET короче 32 символов — "
                "без него любой может слать боту поддельные апдейты"
            )
    return problems
