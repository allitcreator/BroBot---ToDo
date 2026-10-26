# HANDOFF

Статус: открыт
Обновлено: 2026-10-26 17:50 MSK
Клиент: Claude

## Цель

Мини-апп ToDoBOT: кидаешь в чат что угодно → без даты ложится во «Входящие» →
разбираешь в мини-аппе. Плюс экраны «Сегодня/Неделя», проекты, настройки,
статистика. План и этапы — `MINIAPP-PLAN.md`, аудит перед ним —
`AUDIT-2026-10-26.md`. История проектов и выключателя — `HANDOFF-archive.md`.

## Выполнено

- Аудит кода и vps4 (read-only), критика плана через `skeptic`, замеры Graph
  из прод-контейнера. Коммит `bfdff02`.
- Решения Александра: To Do на телефоне почти не открывает → экраны задач
  нужны; разбор входящих только в мини-аппе; нет даты → входящие, дата
  пишется комментарием к форварду; ненужное удаляется; поддомен
  `todo.alxforbot.online`, A-запись заведена в reg.ru.
- Этап A, коммит `4895e38`, **задеплоен в прод 2026-10-26**:
  - замок на рефреш токенов MS и Google, `_force_refresh` после 401;
  - `AuthExpiredError` с подсказкой `get_tokens.py`; ротированный
    refresh-токен MS держится в памяти (`_current_refresh_token`);
  - LLM: `timeout=45, connect=5`, `max_retries=1`; разведка — 90 с через
    `with_options`; `classify_message` логирует сбой;
  - `config.config_problems()` + выход на старте в `bot.py`;
  - `.dockerignore`, compose с `127.0.0.1:8081` и ротацией логов,
    `.gitignore` на `.env*` и `backups/`;
  - `deploy/backup-db.sh` — снимок `bot.db` через sqlite3 backup API,
    integrity_check, gzip, 14 дней.

- Этап B, **задеплоен в прод 2026-10-26** (прод — `db49653`):
  - `e43e3da` бот: нет даты → входящие (время без дня — сегодня), форвард
    без комментария через 2 с → входящие, ответ со счётчиком и кнопками
    «Сегодня/Завтра» (`inbox:*`), `/skip` чистит `saved_forwards`,
    `ms_todo.get_inbox_tasks`;
  - `4ce5781` мини-апп: `api/auth.py` (initData, формат hash, срок 2 ч),
    `api/routes.py` (`/api/inbox`, `/api/tasks/{key}/schedule`,
    `DELETE /api/tasks/{key}`, `/health`), `services/task_ops.py`
    (чистка хвостов задачи), кэш срезов 60 с в `ms_todo`, кнопка меню и
    `/app` по `MINIAPP_URL`, `miniapp/` (React + Vite), `deploy/nginx-todo.conf`,
    `deploy/deploy-front.sh` (цели `front`, `nginx`, `webhook`).
  - Security-ревью: обхода auth нет; шесть Low исправлены (формат hash,
    срок подписи, `%r` пути в логах, CSP со script-src, заголовки в каждом
    location, limit_req, проверка замены вебхука, бэкапы nginx в
    `/root/nginx-backup`, откат первого запуска, `npm ci --ignore-scripts`).

## Текущее состояние

Прод на vps4 — `96e6f6e`, образ пересобран 2026-10-26 13:26 UTC.

- В `/app` образа больше нет `.env`, копий `bot.db` и `.git`. Старый образ
  с секретами удалён (`docker image rm 47cd54ca…`).
- Логи: `max-size 10m, max-file 3`. Порт `127.0.0.1:8081`.
- Подделанный POST без секрета — 401. Вебхук установлен.
- Смоук в контейнере: три параллельных чтения Graph прошли (6 на сегодня,
  0 просроченных, 16 открытых), токен Google получен, классификатор
  ответил `task`.
- Бэкап: `backups/bot-20261026-1329.db.gz` (32 КБ), cron `30 4 * * *`,
  лог `/var/log/todobot-backup.log`. Копия перед деплоем —
  `data/bot.db.bak-20261026`. Все `.bak` теперь 600.
- Версии после сборки: aiogram 3.31.0, aiohttp 3.14.5, httpx 0.28.1,
  openai 3.37.0 (было 3.8.0 — версии не закреплены, находка аудита).

Наблюдения:
- Холодный старт ~2 мин при нагрузке: импорт модулей на одном ядре под
  load average 6 занял ~48 с (openai — ~9 с). В это время вебхук не
  отвечает, Telegram доставит апдейты позже.
- Диск 48% → 61%: параллельно пересобирался foodbot (образ 678 МБ, build
  cache 2.5 ГБ, из них 592 МБ можно освободить). Это не todobot, не чистил.

A-запись `todo.alxforbot.online`: в 16:00 MSK авторитетный `ns1.reg.ru` её ещё
не отдавал. Проверить: `dig +short todo.alxforbot.online @ns1.reg.ru`.

## Решения и ограничения

- API мини-аппа — в процессе бота, фронт — статикой через nginx, схема
  splitbot (`../bot_spliwise`, `deploy/deploy.sh`, `backend/app/api/auth.py`).
- Вебхук перед Этапом B перевести на точный путь
  (`location = /webhook/todobot/`): сейчас nginx отрезает префикс, и `/api`
  стал бы доступен вторым входом.
- Статистику завершённых не чинить пагинацией: 1520 завершённых = 16 страниц
  = 27 с. Фильтр по дате или свой счётчик.
- Ротированный refresh-токен MS хранится только в памяти: после рестарта
  снова берётся из `.env`. Поведение не хуже прежнего; если `.env`-токен
  протухнет — бот скажет «перевыпусти через get_tokens.py».
- Docker локально не установлен — сборка образа проверяется только на VPS.

## Изменённые файлы

`AUDIT-2026-10-26.md`, `MINIAPP-PLAN.md`, `HANDOFF-archive.md` (новые);
`bot.py`, `config.py`, `services/ms_todo.py`, `services/google_calendar.py`,
`services/llm.py`, `services/research.py`, `docker-compose.yml`,
`.gitignore`, `.dockerignore`, `deploy/backup-db.sh`, `tests/test_hardening.py`.

## Проверки

- `venv/bin/python -m pytest tests -q` — 64 passed. 10 новых в
  `tests/test_hardening.py` сначала падали все, потом позеленели.
- `deploy/backup-db.sh` прогнан на временной БД: снимок создан, права 600,
  после gunzip данные читаются.
- Бот локально не запускался: `load_dotenv` найдёт боевой `.env`, а второй
  экземпляр помешает проду.

## Блокеры и риски

- Живой прогон в Telegram после деплоя этапа A не сделан.
- vps4 совмещён с LV-выходом VPN: правки nginx только через `nginx -t`.

## Этап C в проде (2026-10-26, `f6755fe`)

Вкладки «Входящие / Сегодня / Неделя» со счётчиками. API: `GET /api/tasks?view=today|week`,
`GET /api/summary`, `POST /api/tasks/{key}/complete` (с чисткой хвостов),
`POST /api/tasks/move` (итог «X из Y»). Фронт разнесён: `screens/`,
`components/TaskCard.tsx`, `hooks.ts` (оптимистичные действия), `format.ts`.
Факт: Graph хранит у задач только дату (все сроки — полночь UTC, `00:00`
или `21:00`), времени нет — в мини-аппе значки 🔔/🗓 вместо времени.
Проверено: старт с первой попытки, новые эндпоинты без подписи — 401.

## Правило входящих пересмотрено (2026-10-26, прод — `dec9127`)

После первого прогона Александр поменял правило: написанное руками —
задача, без даты на сегодня; любой форвард (с комментарием или без) — во
входящие, дата из комментария не учитывается. Ответ без кнопок, со
счётчиком. Форварды копятся пачкой в памяти (`_batches` в
`handlers/messages.py`, 2 с тишины), текст ждёт 1 с (`COMMENT_WAIT`), не
придёт ли к нему форвард. `saved_forwards` больше не используется.
Кнопки `inbox:*` в старых сообщениях чата продолжают работать.

Мини-апп (`dec9127`): «Дата» открывает поле с «Перенести/Отмена» — на iOS
пикер при открытии сам ставил сегодня и шлёл change, задача уезжала на
сегодня от одного тапа.

## Выкатка этапа B (2026-10-26)

- Копия БД `data/bot.db.bak-20261026-B`; `MINIAPP_URL` дописан в `.env`.
- Первый старт после сборки упал: `TelegramNetworkError ... Connection reset
  by peer` на вызове к api.telegram.org. Сеть через минуту работала
  (curl 302, из контейнера 200), но процесс завис живым без порта, docker
  не перезапускал. Простой ~4 мин, снят `docker compose restart`. Причина
  закрыта коммитом `db49653`: повторы с паузой, выход с кодом 1, если
  вебхук не встал. Повторный старт — с первой попытки.
- Сертификат `todo.alxforbot.online` выпущен (аккаунт LE основного домена:
  на сервере их два, без `--account` certbot не выбирает). Конфиг
  `/etc/nginx/sites-available/todo`, статика `/var/www/todobot`.
- `bots`: `location = /webhook/todobot/` (бэкап `/root/nginx-backup/bots.nginx.bak`).
- Проверено снаружи: мини-апп 200, `/health` 200, `/api/inbox` без подписи
  и с поддельной — 401, `alxforbot.online/webhook/todobot/api/inbox` — 404,
  вебхук без секрета — 401, CSP/HSTS на месте, `/app/` foodbot — 200.

## Следующий точный шаг

1. Живой прогон Александром: кнопка меню «Входящие» (может потребоваться
   перезапуск чата), «купить молоко» → входящие, форвард без комментария →
   входящие, разбор в мини-аппе на телефоне и в Telegram Desktop/Web.
2. Неделя метрики «положено против разобрано».
3. Этап D — проекты, настройки, статистика (см. `MINIAPP-PLAN.md`).
4. Из аудита ещё открыто: `USER` в Dockerfile, healthcheck, `dp.errors`,
   тихие `except`, статистика завершённых, закрепление версий.

## Rollback

Этап B: `git revert 4ce5781 e43e3da`, на vps4 pull + compose; nginx —
`rm /etc/nginx/sites-enabled/todo && systemctl reload nginx`, вебхук —
`cp /root/nginx-backup/bots.nginx.bak /etc/nginx/sites-available/bots`.
Задачи, созданные без даты, остаются в To Do без срока — это валидное
состояние, чинить не нужно.

Этап A:

Код: `git revert 4895e38 && git push origin main`, на vps4
`git pull && docker compose up -d --build`. Схема БД не менялась; копия
перед деплоем — `data/bot.db.bak-20261026`.
Cron бэкапа: удалить строку `todobot/deploy/backup-db.sh` из `crontab -e`.
