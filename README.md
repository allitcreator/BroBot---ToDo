# BroBot — ToDo

Telegram-бот для управления задачами через Microsoft Todo и Google Calendar.

## Возможности

- **Текстовые задачи** — напиши задачу в чат, бот распарсит через LLM и создаст в MS Todo
- **Голосовые сообщения** — надиктуй задачу, бот транскрибирует и создаст задачу
- **Пересланные сообщения** — перешли сообщение + напиши комментарий → задача с описанием и ссылкой на оригинал
- **Google Calendar** — автоматическое предложение добавить событие для встреч, приёмов, созвонов
- **Подзадачи** — если в сообщении есть список, бот создаст checklist
- **Управление задачами** — inline-кнопки: выполнить, редактировать название, изменить дату

## Команды

| Команда | Описание |
|---------|----------|
| `/todotoday` | Задачи на сегодня |
| `/todoall` | Все открытые задачи |
| `/overdue` | Просроченные задачи |
| `/week` | События в Google Calendar на неделю |
| `/stats` | Статистика |
| `/settings` | Настройки (режим подтверждения) |

## Стек

- **Python 3.12** + **aiogram 3.x** — Telegram Bot API
- **OpenRouter** (Gemini Flash) — парсинг задач и транскрипция голосовых
- **MS Graph API** — Microsoft Todo
- **Google Calendar API** — события
- **SQLite** (aiosqlite) — состояния бота
- **Docker** — деплой

## Быстрый старт

```bash
# Клонировать
git clone https://github.com/allitcreator/BroBot---ToDo.git
cd BroBot---ToDo

# Настроить окружение
cp .env.example .env
# Заполнить .env (токены, ключи API)

# Запустить через Docker
docker compose up -d --build

# Или локально
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Настройка

Скопируй `.env.example` в `.env` и заполни:

| Переменная | Описание |
|-----------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather |
| `TELEGRAM_USER_ID` | Твой Telegram ID |
| `OPENROUTER_API_KEY` | Ключ OpenRouter |
| `MS_CLIENT_ID` | Azure App Client ID |
| `MS_CLIENT_SECRET` | Azure App Client Secret |
| `MS_REFRESH_TOKEN` | OAuth refresh token (получить через `get_tokens.py`) |
| `MS_TODO_LIST_ID` | ID списка в MS Todo (получить через `get_lists.py`) |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `GOOGLE_REFRESH_TOKEN` | Google refresh token (получить через `get_tokens.py`) |
| `GOOGLE_CALENDAR_ID` | ID календаря (по умолчанию `primary`) |

### Получение токенов

```bash
python get_tokens.py
```

Скрипт проведёт через OAuth-флоу для Microsoft и Google.

## Деплой на VPS

Подробная инструкция с webhook, nginx и SSL — в [DEPLOY.md](DEPLOY.md).
