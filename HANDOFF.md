# HANDOFF

Статус: открыт
Обновлено: 2026-10-28 12:10 MSK
Клиент: Claude

## Цель

Сводное состояние ToDoBOT после двух параллельных линий работы 26–27.10:
ритуалы и UI мини-аппа (Claude в ToDoBOT) и этап 4 ассистента (Claude из
`../todo_and_run` по `../todo_and_run/ASSISTANT-BOT-PLAN.md`). Новой
разработки в работе нет; открыты только хвосты ниже. Подробности обеих
сессий дословно лежат в `HANDOFF-archive.md`: разделы «ритуалы» и «этап 4
ассистента».

## Выполнено

Прод на vps4: `11b7909` (main), мини-апп `https://todo.alxforbot.online`.
В проде по порядку выкладки:
- **Ритуалы** (`9465ca6..a56a836`, `d920075`, этапы 0–5 `RITUAL-PLAN.md`):
  утренняя (9:30) и вечерняя (20:30) сводки с кнопкой в мини-апп, шаги
  «Просрочено / Застряло / Входящие / План дня» и «Что успел / Остальное
  на завтра»; вход и с экрана «Сегодня». Перенос задачи двигает
  напоминания и событие календаря.
- **UI в стиле Things 3** (`f8558c4`, `4650a40`, только фронт): строки с
  чекбоксом и раскрытием, ссылки в названиях — короткие метки
  (`components/Title.tsx`), без вкладки проектов.
- **Проекты выключены** и в чате: `projects_enabled = False` (решение
  Александра 27.10), включаются в `/settings` бота.
- **Этап 4 ассистента** (`11b7909`):
  - 3a — напоминания, поставленные с Mac в MS Todo, синк раз в 5 мин
    (`services/reminder_sync.py`, `_sync_tick`), кнопки «✅ Готово / +1 ч /
    Завтра», таблицы `reminder_log`, `sync_state`, колонки `reminders`
    (`source`, `attempts`, `sent_at`);
  - 3b — `POST /api/assistant/materials` по Bearer
    (`api/material_routes.py`), сообщение «📖 Новое»;
  - 3c — вкладка «📖 Чтение» в мини-аппе, свой рендер markdown без HTML.
  - `deploy/nginx-todo.conf`: `location /api/assistant/` с
    `client_max_body_size 300k`.

## Текущее состояние

- Ритуалы: 27.10 утром одна запись `ritual_runs` morning `skipped` (решать
  было нечего), дублей нет. Настоящая отправка сводки и открытие
  `?ritual=` из кнопки в Telegram в документах не подтверждены; на 28.10
  прод не перепроверял — чтение с vps4 запрещено auto-режимом.
- Этап 4 принят на проде 27.10: Александр открыл «📖 Новое», прочитал и
  удалил материал; напоминание с Mac пришло один раз; закрытая задача синком
  не воскресла; ошибок в логах не было.

## Решения и ограничения

- Выкладка только по явному «выкатывай» Александра, на каждый деплой.
- Docker локально нет; бот локально не запускать (подхватит боевой `.env`).
- Александр не проверяет руками: приёмка — тесты и смоук на проде.
- Graph хранит у задач только дату, время берётся из напоминаний бота и
  событий Google Calendar.
- `ASSISTANT_API_TOKEN` (≥32 символов) — в `.env` vps4 и Mac. Строку в
  `.env.example` решили не добавлять (28.10). Пустой токен — `/api/assistant/*`
  отвечает 404.
- Markdown во «Чтении»: курсив только `*…*` (lookbehind ломает iOS < 16.4).
- «📖 Новое» не повторяется при сбое Telegram (`notified:false`).

## Изменённые файлы

Не закоммичено (порядок дня): новые `services/task_meta.py`,
`deploy/strip_task_meta.py`, `tests/test_task_meta.py`,
`tests/test_api_plan.py`, `tests/fixtures/task_meta_cases.json`,
`miniapp/src/components/DayPlanner.tsx`, `miniapp/src/DayPlan.test.tsx`,
`TODAY-ORDER-CONCEPT.md`; изменены `api/routes.py`, `api/ritual_routes.py`,
`api/more_routes.py`, `services/ms_todo.py`, `services/projects.py`,
`services/task_ops.py`, `tests/test_api_ritual.py`, `miniapp/src/api.ts`,
`miniapp/src/screens/{RitualMorning,TodayScreen}.tsx`,
`miniapp/src/styles.css`. Плюс `HANDOFF.md`, `HANDOFF-archive.md`.

## Проверки

- Последний полный прогон (27.10, этап 4): pytest 302 passed, vitest 119
  passed, `tsc --noEmit`, `npm run build`.
- Ритуалы на проде 26.10: `smoke_rituals.py` 11/11,
  `smoke_reschedule.py` 12/12. Запуск: `ssh vps4 "cd ~/todobot && docker
  compose exec -T todobot python -" < deploy/smoke_rituals.py`.
- Ревью: `reviewer` на каждый этап ритуалов; этап 4 — ревью кода (10) и
  security-review (7), всё исправлено с тестами.

## Блокеры и риски

- Не проверено живьём: настоящая сводка ритуала с кнопкой, реальный Graph
  `reminderDateTime` round-trip, nginx-лимит 300k на большом материале.
- Пуш приложения To Do приходит параллельно с напоминанием бота.
- Пропуск сводки (`skipped`) не пишется в лог, виден только в `ritual_runs`.
- Из аудита (`AUDIT-2026-10-26.md`) открыто: `USER` в Dockerfile,
  healthcheck, `dp.errors`, тихие `except`, закрепление версий,
  статистика 2.5 с (три запроса подряд).

## Следующий точный шаг

0. **В работе (28.10): порядок дня и исполнитель задачи** — план
   `TODAY-ORDER-CONCEPT.md` (решения Александра и ревью skeptic учтены).
   Этап 1 (бэкенд) и этап 2 (мини-апп) написаны, не закоммичены, не в проде:
   pytest 351, vitest 131, `tsc`, сборка. Ревью бэкенда (3) и фронта (2+2)
   — находки исправлены с тестами. Дальше: коммит → выкладка по
   «выкатывай» → смоук на тестовой задаче. Этап 3
   (ассистент, `../todo_and_run`) — после решения Александра, кто делает.
1. С разрешения на чтение прода: `ritual_runs` за 27–28.10 — ушла ли хоть
   одна сводка `sent`, и `grep 'ritual=' /var/log/nginx/access.log`.
2. `logger.info` на пропуск сводки в `services/rituals.py`.
3. Хвосты аудита — по выбору Александра.

## Rollback

- Этап 4: `git revert 11b7909`; новые таблицы и колонки старый код не трогает.
- UI Things 3: `git revert 4650a40 f8558c4` (только фронт).
- Ритуалы: `git revert` `d920075` и `9465ca6..a56a836`.
- После revert: push, на vps4 `git pull && docker compose up -d --build`,
  `deploy/deploy-front.sh front`.
- БД: `gunzip` снимка из `/root/todobot/backups/` (перед этапом 4 —
  `bot-20261027-1442.db.gz`, перед ритуалами — `bot-20261026-2033.db.gz`)
  в `data/bot.db` при остановленном контейнере.
- Откат этапов A–D — в `HANDOFF-archive.md`.
