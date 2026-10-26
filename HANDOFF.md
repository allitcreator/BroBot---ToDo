# HANDOFF

Статус: открыт
Обновлено: 2026-10-26 16:35 MSK
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

## Следующий точный шаг

1. Живой тест в Telegram: обычная задача, `/todotoday`, `/stats`.
2. Утром 2026-10-27 проверить, что cron снял снимок:
   `ssh vps4 'tail -3 /var/log/todobot-backup.log'`.
3. Этап B из `MINIAPP-PLAN.md`: сначала бот (нет даты → входящие, форвард без
   комментария → входящие), затем скелет мини-аппа и экран «Входящие».

## Rollback

Код: `git revert 4895e38 && git push origin main`, на vps4
`git pull && docker compose up -d --build`. Схема БД не менялась; копия
перед деплоем — `data/bot.db.bak-20261026`.
Cron бэкапа: удалить строку `todobot/deploy/backup-db.sh` из `crontab -e`.
