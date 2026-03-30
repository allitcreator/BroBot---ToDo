# Деплой Telegram-ботов на VPS с Webhook

Один домен, один nginx, несколько ботов в отдельных Docker-контейнерах.

```
Telegram --> https://bots.example.com/webhook/todobot/  --> nginx --> 127.0.0.1:8081 --> контейнер todobot
Telegram --> https://bots.example.com/webhook/otherbot/ --> nginx --> 127.0.0.1:8082 --> контейнер otherbot
```

---

## Требования

- VPS с Ubuntu 22.04+ (минимум 1 vCPU, 1 GB RAM)
- Публичный IP-адрес
- Домен (например, `bots.example.com`) с A-записью на IP сервера

---

## Шаг 1. Настройка VPS

```bash
ssh root@<IP>

# Обновляем систему
apt update && apt upgrade -y

# Docker
curl -fsSL https://get.docker.com | sh

# Nginx + Certbot (SSL) + Git
apt install -y nginx certbot python3-certbot-nginx git
```

---

## Шаг 2. SSL-сертификат (Let's Encrypt)

Домен уже должен указывать на IP сервера (A-запись).

```bash
# Получаем сертификат
certbot --nginx -d bots.example.com

# Проверяем автопродление
certbot renew --dry-run
```

---

## Шаг 3. Настройка Nginx

Создаём файл `/etc/nginx/sites-available/bots`:

```nginx
server {
    listen 443 ssl;
    server_name bots.example.com;

    ssl_certificate /etc/letsencrypt/live/bots.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bots.example.com/privkey.pem;

    # ToDo бот
    location /webhook/todobot/ {
        proxy_pass http://127.0.0.1:8081/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_remote_addr;
    }

    # Второй бот (добавлять по аналогии)
    # location /webhook/otherbot/ {
    #     proxy_pass http://127.0.0.1:8082/;
    #     proxy_set_header Host $host;
    #     proxy_set_header X-Forwarded-For $proxy_remote_addr;
    # }
}
```

Активируем:

```bash
ln -s /etc/nginx/sites-available/bots /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## Шаг 4. Изменения в коде бота

### 4.1. `config.py` — webhook-переменные

Добавить в конец файла:

```python
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"
```

### 4.2. `bot.py` — поддержка polling + webhook

```python
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import config
from db import storage
from handlers import messages, commands, callbacks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def on_startup(bot: Bot):
    webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set: {webhook_url}")


async def on_shutdown(bot: Bot):
    from services import ms_todo, google_calendar
    await ms_todo.close()
    await google_calendar.close()
    await storage.close_db()
    await bot.delete_webhook()


async def main():
    await storage.init_db()

    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(messages.router)

    await bot.set_my_commands([
        BotCommand(command="todotoday", description="Задачи на сегодня"),
        BotCommand(command="todoall", description="Все открытые задачи"),
        BotCommand(command="overdue", description="Просроченные задачи"),
        BotCommand(command="week", description="События на неделю"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="settings", description="Настройки"),
    ])

    if config.USE_WEBHOOK:
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

        app = web.Application()
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=config.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        logging.info(f"Starting webhook on {config.WEBAPP_HOST}:{config.WEBAPP_PORT}")
        web.run_app(app, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)
    else:
        logging.info("Bot started (polling)")
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        finally:
            from services import ms_todo, google_calendar
            await ms_todo.close()
            await google_calendar.close()
            await storage.close_db()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### 4.3. `requirements.txt` — добавить aiohttp

```
aiohttp>=3.9.0
```

### 4.4. `.env.example` — добавить webhook-переменные

```env
# Webhook (для VPS, локально не нужно)
USE_WEBHOOK=false
WEBHOOK_HOST=https://bots.example.com
WEBHOOK_PATH=/webhook/todobot/
WEBAPP_HOST=0.0.0.0
WEBAPP_PORT=8080
```

### 4.5. `docker-compose.yml` — открыть порт

```yaml
services:
  todobot:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "8081:8080"
    volumes:
      - ./data:/app/data
```

Каждый бот маппит свой внешний порт (8081, 8082, ...) на внутренний 8080.

---

## Шаг 5. Деплой

```bash
cd ~
git clone <repo-url> todobot
cd todobot

# Настраиваем окружение
cp .env.example .env
nano .env   # заполнить ключи + установить USE_WEBHOOK=true

# Запускаем
docker compose up -d --build

# Проверяем логи
docker compose logs -f
```

---

## Шаг 6. Проверка

```bash
# Статус webhook
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool
```

Должно показать:
- `"url": "https://bots.example.com/webhook/todobot/"`
- `"pending_update_count": 0`
- `"last_error_message": ""` (пусто = всё ок)

---

## Добавление нового бота

1. Создать код бота в отдельной папке (`~/otherbot/`)
2. В `.env` указать: `USE_WEBHOOK=true`, `WEBHOOK_PATH=/webhook/otherbot/`
3. В `docker-compose.yml` маппить другой порт: `"8082:8080"`
4. В nginx добавить location:
   ```nginx
   location /webhook/otherbot/ {
       proxy_pass http://127.0.0.1:8082/;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_remote_addr;
   }
   ```
5. `nginx -t && systemctl reload nginx`
6. `docker compose up -d --build`

---

## Обновление бота

```bash
cd ~/todobot
git pull
docker compose up -d --build
```

---

## Локальная разработка

По умолчанию `USE_WEBHOOK=false` — бот работает в режиме polling.
Домен, SSL и nginx не нужны. Просто `python bot.py`.
