# Деплой Telegram-ботов на VPS с Webhook

Один домен, один nginx, несколько ботов в отдельных Docker-контейнерах.

```
Telegram --> https://alxthecreatortg.ru/webhook/todobot/  --> nginx --> 127.0.0.1:8081 --> контейнер todobot
Telegram --> https://alxthecreatortg.ru/webhook/otherbot/ --> nginx --> 127.0.0.1:8082 --> контейнер otherbot
```

---

## Требования

- VPS с Ubuntu 22.04+ (минимум 1 vCPU, 1 GB RAM)
- Публичный IP-адрес
- Домен с A-записью на IP сервера

---

## Шаг 1. Настройка VPS

```bash
ssh root@<IP>

# Обновляем систему
apt update && apt upgrade -y

# Docker
curl -fsSL https://get.docker.com | sh

# Добавляем пользователя в группу docker (чтобы не нужен sudo)
usermod -aG docker <username>

# Nginx + Certbot (SSL) + Git
apt install -y nginx certbot python3-certbot-nginx git
```

---

## Шаг 2. Домен

Настроить A-запись у регистратора домена:

| Тип | Имя | Значение |
|-----|-----|----------|
| A | @ | IP вашего VPS |

DNS обновляется 5-15 минут. Проверка:
```bash
dig +short yourdomain.ru
# Должен показать IP вашего VPS
```

---

## Шаг 3. SSL-сертификат (Let's Encrypt)

```bash
# Получаем сертификат (домен уже должен указывать на IP)
sudo certbot --nginx -d yourdomain.ru

# Проверяем автопродление
sudo certbot renew --dry-run
```

Сертификат продлевается автоматически через cron.

---

## Шаг 4. Настройка Nginx

Удаляем дефолтный конфиг и создаём `/etc/nginx/sites-available/bots`:

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name yourdomain.ru;

    ssl_certificate /etc/letsencrypt/live/yourdomain.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.ru/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # ToDo бот (порт 8081)
    location /webhook/todobot/ {
        proxy_pass http://127.0.0.1:8081/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    # Второй бот (порт 8082) — добавлять по аналогии
    # location /webhook/otherbot/ {
    #     proxy_pass http://127.0.0.1:8082/;
    #     proxy_set_header Host $host;
    #     proxy_set_header X-Forwarded-For $remote_addr;
    # }
}

server {
    listen 80;
    listen [::]:80;
    server_name yourdomain.ru;
    return 301 https://$host$request_uri;
}
```

Активируем:

```bash
sudo rm /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/bots /etc/nginx/sites-enabled/bots
sudo nginx -t && sudo systemctl reload nginx
```

**Важно:** `proxy_pass` с trailing slash (`http://127.0.0.1:8081/`) стрипает prefix `/webhook/todobot/` — в контейнер приходит запрос на `/`. Поэтому бот слушает на `/`, а не на `/webhook/todobot/`.

---

## Шаг 5. Клонирование и настройка

```bash
cd ~
git clone https://github.com/allitcreator/BroBot---ToDo.git todobot
cd todobot

# Создаём .env
cp .env.example .env
nano .env
```

### Переменные .env для VPS

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
OPENROUTER_API_KEY=...

MS_CLIENT_ID=...
MS_CLIENT_SECRET=...
MS_REFRESH_TOKEN=...
MS_TODO_LIST_ID=...

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=primary

DB_PATH=data/bot.db
USER_TIMEZONE=Europe/Moscow

USE_WEBHOOK=true
WEBHOOK_HOST=https://yourdomain.ru
WEBHOOK_PATH=/webhook/todobot/
WEBAPP_HOST=0.0.0.0
WEBAPP_PORT=8080
```

---

## Шаг 6. Запуск

```bash
cd ~/todobot
docker compose up -d --build
```

Проверка логов:
```bash
docker compose logs -f
```

Должно показать:
```
Starting webhook on 0.0.0.0:8080
Webhook set: https://yourdomain.ru/webhook/todobot/
```

---

## Шаг 7. Проверка webhook

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool
```

Успешный ответ:
```json
{
    "url": "https://yourdomain.ru/webhook/todobot/",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "ip_address": "...",
    "last_error_message": ""
}
```

Отправьте сообщение боту в Telegram — он должен ответить.

---

## Обновление бота

```bash
cd ~/todobot
git pull origin main
docker compose up -d --build
```

---

## Добавление нового бота

1. Клонировать/создать код в отдельную папку (`~/otherbot/`)
2. В `.env` указать: `USE_WEBHOOK=true`, `WEBHOOK_PATH=/webhook/otherbot/`
3. В `docker-compose.yml` маппить другой внешний порт: `"8082:8080"`
4. В nginx добавить location:
   ```nginx
   location /webhook/otherbot/ {
       proxy_pass http://127.0.0.1:8082/;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $remote_addr;
   }
   ```
5. `sudo nginx -t && sudo systemctl reload nginx`
6. `docker compose up -d --build`

---

## Локальная разработка

По умолчанию `USE_WEBHOOK=false` — бот работает в режиме polling.
Домен, SSL и nginx не нужны:

```bash
python bot.py
```

---

## Структура проекта

```
todobot/
├── bot.py                 # Точка входа (polling / webhook)
├── config.py              # Переменные окружения
├── requirements.txt       # Зависимости Python
├── Dockerfile             # Docker-образ
├── docker-compose.yml     # Запуск контейнера
├── .env                   # Секреты (не в git)
├── .env.example           # Шаблон секретов
├── data/                  # SQLite БД (volume, не в git)
├── db/
│   └── storage.py         # Работа с SQLite
├── handlers/
│   ├── messages.py        # Обработка сообщений и голосовых
│   ├── commands.py        # /todotoday, /week, /stats и др.
│   ├── callbacks.py       # Inline-кнопки
│   ├── keyboards.py       # Клавиатуры
│   └── utils.py           # Утилиты форматирования
├── services/
│   ├── llm.py             # OpenRouter (Gemini Flash): парсинг + транскрипция
│   ├── ms_todo.py         # MS Graph API: задачи
│   └── google_calendar.py # Google Calendar API: события
├── get_tokens.py          # OAuth-флоу для получения refresh-токенов
└── get_lists.py           # Получение ID списка MS Todo
```

---

## Полезные команды

```bash
# Логи бота
docker compose logs -f

# Перезапуск
docker compose restart

# Остановка
docker compose down

# Пересборка с нуля
docker compose up -d --build --force-recreate

# Статус webhook
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```
