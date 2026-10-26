# HANDOFF

Статус: открыт
Обновлено: 2026-10-26 16:40 MSK
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
- Этап A, коммит `4895e38` (локально, **не запушен, не задеплоен**):
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

Прод на vps4 — по-прежнему `5de9f93`, образ от 2026-09-08. В нём лежат
`.env`, `.env.bak-20260824` и копии `bot.db` (находка аудита) — уйдут после
пересборки с `.dockerignore`.

На VPS `docker-compose.yml` правлен руками (`127.0.0.1:8081`) — `git pull`
на него упрётся. Перед pull: `git checkout docker-compose.yml`, содержимое в
новом коммите то же плюс ротация логов.

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

- Деплой этапа A ждёт подтверждения Александра.
- vps4 совмещён с LV-выходом VPN: правки nginx только через `nginx -t`.

## Следующий точный шаг

Деплой этапа A на vps4:

```
git push origin main
ssh vps4
cd /root/todobot
cp data/bot.db data/bot.db.bak-20261026
git checkout docker-compose.yml && rm docker-compose.yml.bak-20261023
git pull
docker compose up -d --build
docker logs --tail 20 todobot-todobot-1
docker exec todobot-todobot-1 ls -a /app      # .env и data-копий быть не должно
docker image prune -f                          # старый образ с секретами
deploy/backup-db.sh                            # первый снимок руками
(crontab -l; echo '30 4 * * * /root/todobot/deploy/backup-db.sh >> /var/log/todobot-backup.log 2>&1') | crontab -
```

Потом: живой тест в Telegram (обычная задача), затем Этап B.

## Rollback

Код: `git revert 4895e38 && git push origin main`, на vps4
`git pull && docker compose up -d --build`. Схема БД не менялась.
Cron бэкапа: удалить строку `todobot/deploy/backup-db.sh` из `crontab -e`.
