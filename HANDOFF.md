# HANDOFF

Статус: ждёт — выкладка этапа 4 ассистента: решение и действия Александра на vps4
Обновлено: 2026-10-27 17:14 MSK
Клиент: Claude

## Цель

Этап 4 ассистента `../todo_and_run` по `../todo_and_run/ASSISTANT-BOT-PLAN.md`:
3a — напоминания с Mac доходят в Telegram (синк из MS Todo, кнопки
«✅ Готово / +1 ч / Завтра», отправка без вечных ретраев); 3b — приём
материалов `POST /api/assistant/materials` по Bearer; 3c — вкладка
«📖 Чтение» в мини-аппе. Прошлая сессия (ритуалы) — в `HANDOFF-archive.md`.

## Выполнено

Код в ветке `assistant-reminders-sync` (от `main`), **не закоммичен**:
14 изменённых файлов (+592/−27) и 11 новых — `services/reminder_sync.py`,
`api/material_routes.py`, `miniapp/src/{markdown.tsx,reading.ts,
screens/ReadingScreen.tsx,components/ErrorBoundary.tsx}`, тесты.
- 3a: синк раз в 5 мин (`_sync_tick`), `reminders.source`
  (`sync|mstodo|button|tg|NULL`), `attempts`, `sent_at`; `reminder_log`
  (`kind sent|suppress`), `sync_state` (окно прошедших от последнего
  успешного синка, ≤6 ч; после выкладки — 0). Постоянные ошибки Telegram
  снимают сразу, временные не считаются 2 ч от `fire_at`.
- 3b: Bearer только для `/api/assistant/*` (`hmac.compare_digest`, пустой
  токен — 404, короче 32 — `config_problems`), лимит 256 КБ, идемпотентность
  `ON CONFLICT`, ответ `{id, duplicate, notified}`, «📖 Новое» с web_app
  `?read=<id>`.
- 3c: свой рендер markdown без HTML (ссылки только http(s)), пределы
  против ReDoS и вложенности, ErrorBoundary, кэш в localStorage только при
  сетевой ошибке, размер шрифта в «Ещё». `/api/summary` + поле `reading`.
- `deploy/nginx-todo.conf`: `location /api/assistant/` с
  `client_max_body_size 300k`.

## Текущее состояние

Код готов и проверен локально; на проде старая версия `54d5bbf`. Клиент
на Mac готов: `todo.py remind`, `todo.py publish` (в `../todo_and_run`).

## Решения и ограничения

- Курсив только `*…*`, `__bold__` и `_…_` не поддерживаются (lookbehind
  ломает iOS < 16.4, snake_case).
- «📖 Новое» не повторяется при сбое Telegram (`notified:false` в ответе).
- Размер шрифта — localStorage устройства.
- Выкладка только по явному «выкатывай» Александра.

## Изменённые файлы

`git status` в ветке `assistant-reminders-sync`.

## Проверки

- pytest 302 passed (до этапа 213), vitest 119 passed, `tsc --noEmit`,
  `npm run build` — 27.10, перепроверено координатором.
- Ревью: 3a — 8 находок, исправлены; 3a–3c — ревью кода (10) и
  security-review (7: ReDoS, рекурсия, кэш, логи, ошибки, длина токена,
  редирект в клиенте) — исправлены с тестами.
- Не проверено живьём: Telegram-кнопки, реальный Graph
  (`reminderDateTime` round-trip), вкладка на iPhone, nginx.

## Блокеры и риски

- Миграция БД при старте (колонки `reminders`, таблицы `reminder_log`,
  `sync_state`, `materials`) — бэкап `bot.db` до выкладки.
- `ASSISTANT_API_TOKEN` ≥32 символов в `.env` на vps4 и в `.env` на Mac;
  строку в `.env.example` добавить вручную (агентам доступ закрыт).
- Пуш приложения To Do придёт параллельно с напоминанием бота.

## Следующий точный шаг

1. Александр: «выкатывай» или правки. Перед этим — commit в ветке и PR/merge
   в `main` (по его просьбе).
2. Выкладка: `deploy/backup-db.sh`; токен в `.env` vps4; `git pull && docker
   compose up -d --build`; nginx-конфиг + `nginx -t && systemctl reload
   nginx`; `deploy/deploy-front.sh front`.
3. Смоук: `todo.py remind <N> <через 7 мин> --yes` → сообщение с кнопками;
   `todo.py publish knowledge-base/personal/<файл>.md --yes` → «📖 Новое»,
   вкладка на iPhone.

## Rollback

`git checkout main` (ветка не влита). После выкладки — `git revert`
коммитов ветки, `docker compose up -d --build`, `deploy-front.sh front`;
БД — `gunzip` снимка из `deploy/backup-db.sh` в `data/bot.db` при
остановленном контейнере. Новые таблицы и колонки старый код не трогает.
